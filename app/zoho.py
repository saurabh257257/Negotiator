"""Zoho OAuth 2.0 + Zoho Books API client and the bills sync.

Plain deterministic code: no AI is involved in connecting or pulling data.
"""
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import requests

from . import config, db
from .crypto import decrypt, encrypt

ACCOUNTS = {
    "in": "https://accounts.zoho.in",
    "com": "https://accounts.zoho.com",
    "eu": "https://accounts.zoho.eu",
    "au": "https://accounts.zoho.com.au",
    "jp": "https://accounts.zoho.jp",
    "ca": "https://accounts.zohocloud.ca",
    "sa": "https://accounts.zoho.sa",
    "ae": "https://accounts.zoho.ae",
}
DC_LABELS = {
    "in": "India (zoho.in)", "com": "United States (zoho.com)", "eu": "Europe (zoho.eu)",
    "au": "Australia (zoho.com.au)", "jp": "Japan (zoho.jp)", "ca": "Canada (zohocloud.ca)",
    "sa": "Saudi Arabia (zoho.sa)", "ae": "UAE (zoho.ae)",
}
# What we ask the user to approve (read-only). Shown on our connect page and again by Zoho.
SCOPES = [
    ("ZohoBooks.bills.READ", "Read your vendor bills: vendor, bill date, line items, rates and quantities."),
    ("ZohoBooks.settings.READ", "Read your organization details: name and organization ID."),
]
SKIP_STATUSES = {"void", "draft"}


class ZohoError(Exception):
    pass


# ---------------------------------------------------------------- OAuth

def authorize_url(dc: str, state: str) -> str:
    params = {
        "response_type": "code",
        "client_id": config.ZOHO_CLIENT_ID,
        "scope": ",".join(s for s, _ in SCOPES),
        "redirect_uri": config.ZOHO_REDIRECT_URI,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{ACCOUNTS[dc]}/oauth/v2/auth?{urlencode(params)}"


def trusted_accounts_url(candidate: str | None, fallback_dc: str) -> str:
    """Zoho tells the callback which accounts server to use. Only accept known Zoho hosts,
    so the client secret can never be posted to an arbitrary URL."""
    if candidate:
        host = urlparse(candidate).netloc.lower()
        for url in ACCOUNTS.values():
            if urlparse(url).netloc == host:
                return url
    return ACCOUNTS[fallback_dc]


def _token_request(accounts_url: str, data: dict) -> dict:
    r = requests.post(f"{accounts_url}/oauth/v2/token", data=data, timeout=30)
    try:
        body = r.json()
    except ValueError:
        raise ZohoError(f"Zoho token endpoint returned HTTP {r.status_code}")
    if "error" in body or "access_token" not in body:
        raise ZohoError(f"Zoho rejected the request: {body.get('error', 'no access token returned')}")
    return body


def exchange_code(accounts_url: str, code: str) -> dict:
    return _token_request(accounts_url, {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": config.ZOHO_CLIENT_ID,
        "client_secret": config.ZOHO_CLIENT_SECRET,
        "redirect_uri": config.ZOHO_REDIRECT_URI,
    })


def revoke(accounts_url: str, refresh_token: str):
    try:
        requests.post(f"{accounts_url}/oauth/v2/token/revoke", params={"token": refresh_token}, timeout=15)
    except requests.RequestException:
        pass  # best effort: we delete our copy regardless


# ---------------------------------------------------------------- API session

_last_call = 0.0
_call_lock = threading.Lock()


def _throttle():
    global _last_call
    with _call_lock:
        wait = config.ZOHO_MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


class Session:
    """Calls the Zoho Books API for one connected user, refreshing the access token as needed."""

    def __init__(self, mobile: str):
        self.mobile = mobile
        with db.connect() as conn:
            row = conn.execute("SELECT * FROM connections WHERE mobile=?", (mobile,)).fetchone()
        if not row:
            raise ZohoError("Zoho is not connected")
        self.row = dict(row)
        self.access = decrypt(self.row["access_token"])
        self.refresh_token = decrypt(self.row["refresh_token"])
        self.expires_at = self.row["expires_at"]

    def _refresh(self):
        body = _token_request(self.row["accounts_url"], {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
            "client_id": config.ZOHO_CLIENT_ID,
            "client_secret": config.ZOHO_CLIENT_SECRET,
        })
        self.access = body["access_token"]
        self.expires_at = time.time() + int(body.get("expires_in", 3600)) - 60
        with db.connect() as conn:
            conn.execute("UPDATE connections SET access_token=?, expires_at=? WHERE mobile=?",
                         (encrypt(self.access), self.expires_at, self.mobile))

    def get(self, path: str, params: dict | None = None) -> dict:
        if time.time() >= self.expires_at:
            self._refresh()
        url = f"{self.row['api_domain']}/books/v3/{path}"
        refreshed = False
        for attempt in range(5):
            _throttle()
            r = requests.get(url, params=params, timeout=30,
                             headers={"Authorization": f"Zoho-oauthtoken {self.access}"})
            if r.status_code == 401 and not refreshed:
                self._refresh()
                refreshed = True
                continue
            if r.status_code == 429:  # per-minute limit: back off and retry
                time.sleep(15 * (attempt + 1))
                continue
            try:
                body = r.json()
            except ValueError:
                raise ZohoError(f"Zoho returned HTTP {r.status_code} for {path}")
            if r.status_code >= 400 or body.get("code", 0) != 0:
                raise ZohoError(f"Zoho error on {path}: {body.get('message', r.status_code)}")
            return body
        raise ZohoError(f"Zoho kept rate-limiting {path}; try again in a few minutes")


def fetch_organization(api_domain: str, access_token: str) -> tuple[str, str]:
    r = requests.get(f"{api_domain}/books/v3/organizations", timeout=30,
                     headers={"Authorization": f"Zoho-oauthtoken {access_token}"})
    body = r.json()
    orgs = body.get("organizations") or []
    if r.status_code >= 400 or not orgs:
        raise ZohoError(f"Could not read your Zoho Books organization: {body.get('message', r.status_code)}")
    org = next((o for o in orgs if o.get("is_default_org")), orgs[0])
    return str(org["organization_id"]), org.get("name", "")


# ---------------------------------------------------------------- sync

PROGRESS: dict[str, dict] = {}
_sync_lock = threading.Lock()


def sync_running(mobile: str) -> bool:
    return PROGRESS.get(mobile, {}).get("state") == "running"


def start_sync(mobile: str) -> bool:
    with _sync_lock:
        if sync_running(mobile):
            return False
        PROGRESS[mobile] = {"state": "running", "phase": "Listing bills", "done": 0, "total": 0, "message": ""}
    threading.Thread(target=_run, args=(mobile,), daemon=True).start()
    return True


def _run(mobile: str):
    p = PROGRESS[mobile]
    try:
        n_bills, n_lines = sync_bills(mobile, p)
        status = f"OK: {n_bills} bills, {n_lines} line items"
        p.update(state="done", message=status)
    except Exception as exc:  # surfaced to the user on the page
        status = f"Failed: {exc}"
        p.update(state="error", message=str(exc))
    with db.connect() as conn:
        conn.execute("UPDATE connections SET last_sync_at=?, last_sync_status=? WHERE mobile=?",
                     (datetime.now(timezone.utc).isoformat(timespec="seconds"), status, mobile))


def sync_bills(mobile: str, progress: dict) -> tuple[int, int]:
    """Pull bills from the last ~6 months and store one row per bill line item."""
    s = Session(mobile)
    org = s.row["org_id"]
    cutoff = (date.today() - timedelta(days=config.HISTORY_DAYS)).isoformat()

    # 1) List bills, newest first, stopping once a whole page is older than the cutoff.
    #    The list response has no line items, so step 2 fetches each bill.
    bills, page = [], 1
    while True:
        body = s.get("bills", {"organization_id": org, "sort_column": "date", "sort_order": "D",
                               "page": page, "per_page": 200})
        batch = body.get("bills", [])
        recent = [b for b in batch if b.get("date", "") >= cutoff]
        bills += [b for b in recent if b.get("status") not in SKIP_STATUSES]
        progress["message"] = f"Found {len(bills)} bills in the last 6 months"
        if not recent or not body.get("page_context", {}).get("has_more_page"):
            break
        page += 1
    progress.update(phase="Reading bill line items", total=len(bills), done=0)

    # 2) Fetch each bill's line items.
    rows = []
    for i, b in enumerate(bills, 1):
        bill = s.get(f"bills/{b['bill_id']}", {"organization_id": org}).get("bill", {})
        for li in bill.get("line_items", []):
            if li.get("rate") is None:
                continue
            rows.append((
                mobile, b["bill_id"], bill.get("bill_number") or b.get("bill_number"), b["date"], b.get("status"),
                bill.get("vendor_id") or b.get("vendor_id"), bill.get("vendor_name") or b.get("vendor_name"),
                bill.get("currency_code") or b.get("currency_code"),
                li.get("item_id") or "", (li.get("name") or li.get("description") or "(unnamed item)").strip(),
                li.get("sku") or "", li.get("account_name") or "", li.get("product_type") or "",
                li.get("unit") or "", float(li["rate"]), float(li.get("quantity") or 0),
            ))
        progress["done"] = i

    # 3) Replace stored data in one transaction, so a failed sync never wipes good data.
    with db.connect() as conn:
        conn.execute("DELETE FROM bill_lines WHERE mobile=?", (mobile,))
        conn.executemany(
            "INSERT INTO bill_lines (mobile,bill_id,bill_number,bill_date,status,vendor_id,vendor_name,currency,"
            "item_id,item_name,sku,account_name,product_type,unit,rate,quantity) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(bills), len(rows)
