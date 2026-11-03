"""End-to-end test of login -> connect -> OAuth callback -> bills sync -> dashboard,
using a fake Zoho (no network, no real credentials)."""
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest import mock

# Isolated data dir and fake credentials, set before the app modules are imported.
os.environ["NEGOTIATOR_DATA_DIR"] = tempfile.mkdtemp()
os.environ.update(ZOHO_CLIENT_ID="test-client", ZOHO_CLIENT_SECRET="test-secret", ZOHO_MIN_INTERVAL="0",
                  ZOHO_REDIRECT_URI="http://localhost:5055/oauth/callback", AUTH_DEMO="1", DEMO_MOBILE="123")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import analysis, create_app, db, zoho  # noqa: E402
from app.connectors import CONNECTORS  # noqa: E402


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


# (bill_id, days_ago, vendor, currency, status, [(item_id, name, rate, qty, account)])
BILLS = [
    ("b1", 5,   ("v1", "Shenzhen PowerCell"), "USD", "paid", [("i1", "LFP cell 280Ah", 36.0, 100, "Raw Materials")]),
    ("b2", 60,  ("v1", "Shenzhen PowerCell"), "USD", "paid", [("i1", "LFP cell 280Ah", 30.0, 200, "Raw Materials")]),
    ("b3", 20,  ("v2", "Guangzhou EnerCell"), "USD", "open", [("i1", "LFP cell 280Ah", 33.0, 50, "Raw Materials"),
                                                              ("i2", "Epoxy 1kg", 9.5, 40, "Consumables")]),
    ("b4", 90,  ("v3", "Pune PolyMould"),     "INR", "paid", [("i3", "Cell holder 18650 x5", 12.0, 1000, "Raw Materials")]),
    ("b5", 10,  ("v3", "Pune PolyMould"),     "INR", "draft", [("i3", "Cell holder 18650 x5", 99.0, 1, "Raw Materials")]),
    ("b6", 400, ("v3", "Pune PolyMould"),     "INR", "paid", [("i3", "Cell holder 18650 x5", 8.0, 1, "Raw Materials")]),
]
BILLS.sort(key=lambda b: b[1])  # newest first, like sort_column=date&sort_order=D


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def fake_post(url, data=None, params=None, timeout=None):
    if url.endswith("/oauth/v2/token") and data["grant_type"] == "authorization_code":
        assert data["client_id"] == "test-client" and data["client_secret"] == "test-secret"
        if data["code"] != "good-code":
            return FakeResp(200, {"error": "invalid_code"})
        return FakeResp(200, {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600,
                              "api_domain": "https://www.zohoapis.in", "token_type": "Bearer"})
    if url.endswith("/oauth/v2/token") and data["grant_type"] == "refresh_token":
        return FakeResp(200, {"access_token": "AT2", "expires_in": 3600})
    if url.endswith("/oauth/v2/token/revoke"):
        fake_post.revoked = params["token"]
        return FakeResp(200, {})
    raise AssertionError(f"unexpected POST {url}")


def fake_get(url, params=None, headers=None, timeout=None):
    assert headers["Authorization"].startswith("Zoho-oauthtoken ")
    if url.endswith("/books/v3/organizations"):
        return FakeResp(200, {"code": 0, "organizations": [
            {"organization_id": "999", "name": "Other Org", "is_default_org": False},
            {"organization_id": "123456", "name": "Volta Energy", "is_default_org": True}]})
    assert params["organization_id"] == "123456"
    if url.endswith("/books/v3/bills"):
        per, page = params["per_page"], params["page"]
        shown = BILLS[(page - 1) * 3: page * 3]  # tiny pages to exercise pagination
        return FakeResp(200, {"code": 0, "bills": [
            {"bill_id": b[0], "date": days_ago(b[1]), "vendor_id": b[2][0], "vendor_name": b[2][1],
             "status": b[4], "currency_code": b[3], "bill_number": "BN-" + b[0]} for b in shown],
            "page_context": {"page": page, "per_page": per, "has_more_page": page * 3 < len(BILLS)}})
    bill_id = url.rsplit("/", 1)[1]
    b = next(x for x in BILLS if x[0] == bill_id)
    return FakeResp(200, {"code": 0, "bill": {
        "bill_id": b[0], "bill_number": "BN-" + b[0], "vendor_id": b[2][0], "vendor_name": b[2][1],
        "currency_code": b[3], "status": b[4],
        "line_items": [{"item_id": i, "name": n, "rate": r, "quantity": q, "account_name": a, "unit": "pcs",
                        "product_type": "goods"} for i, n, r, q, a in b[5]]}})


class FlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.testing = True

    def setUp(self):
        self.c = self.app.test_client()

    def token(self, path="/login"):
        html = self.c.get(path).get_data(as_text=True)
        return html.split('name="csrf" value="')[1].split('"')[0]

    def login(self):
        self.c.post("/login", data={"mobile": "123", "csrf": self.token()})

    # ---- login
    def test_login_rejects_other_numbers_and_requires_csrf(self):
        t = self.token()
        r = self.c.post("/login", data={"mobile": "999", "csrf": t})
        self.assertIn("isn&#39;t recognised", r.get_data(as_text=True))
        self.assertEqual(self.c.post("/login", data={"mobile": "123"}).status_code, 400)
        self.assertEqual(self.c.get("/connect").status_code, 302)  # not logged in

    def test_demo_login_reaches_connect_page_with_scopes(self):
        self.login()
        html = self.c.get("/connect").get_data(as_text=True)
        self.assertIn("Connect Zoho Books", html)
        self.assertIn("ZohoBooks.bills.READ", html)
        self.assertIn("ZohoBooks.settings.READ", html)
        self.assertNotIn("ZohoBooks.bills.CREATE", html)

    def test_connector_grid_lists_platforms_and_only_zoho_is_functional(self):
        self.login()
        html = self.c.get("/connect").get_data(as_text=True)
        for name in ("Tally Prime", "SAP", "Oracle NetSuite", "QuickBooks", "Xero", "Zoho Books"):
            self.assertIn(name, html)
        ready = [c for c in CONNECTORS if c["ready"]]
        self.assertEqual([c["id"] for c in ready], ["zoho"])
        self.assertEqual(html.count("Coming soon"), len(CONNECTORS) - 1)

    # ---- OAuth
    def start_oauth(self, dc="in"):
        self.login()
        r = self.c.post("/oauth/start", data={"dc": dc, "csrf": self.token("/connect")})
        self.assertEqual(r.status_code, 302)
        return urlparse(r.headers["Location"])

    def test_authorize_redirect_has_scopes_offline_access_and_state(self):
        u = self.start_oauth("in")
        self.assertEqual(u.netloc, "accounts.zoho.in")
        q = parse_qs(u.query)
        self.assertEqual(q["scope"], ["ZohoBooks.bills.READ,ZohoBooks.settings.READ"])
        self.assertEqual(q["access_type"], ["offline"])
        self.assertEqual(q["client_id"], ["test-client"])
        self.assertEqual(q["redirect_uri"], ["http://localhost:5055/oauth/callback"])
        self.assertTrue(q["state"][0])

    def test_callback_rejects_bad_state_and_denied_access(self):
        self.start_oauth()
        r = self.c.get("/oauth/callback?code=good-code&state=WRONG", follow_redirects=True)
        self.assertIn("expired or is invalid", r.get_data(as_text=True))
        self.start_oauth()
        r = self.c.get("/oauth/callback?error=access_denied", follow_redirects=True)
        self.assertIn("not completed", r.get_data(as_text=True))

    def test_client_secret_only_goes_to_known_zoho_hosts(self):
        self.assertEqual(zoho.trusted_accounts_url("https://evil.example.com", "in"), "https://accounts.zoho.in")
        self.assertEqual(zoho.trusted_accounts_url("https://accounts.zoho.eu", "in"), "https://accounts.zoho.eu")

    # ---- full flow
    @mock.patch("app.zoho.requests.post", side_effect=fake_post)
    @mock.patch("app.zoho.requests.get", side_effect=fake_get)
    def test_connect_sync_and_dashboard(self, _get, _post):
        u = self.start_oauth("in")
        state = parse_qs(u.query)["state"][0]
        r = self.c.get(f"/oauth/callback?code=good-code&state={state}&accounts-server=https://accounts.zoho.in")
        self.assertEqual(r.headers["Location"], "/dashboard")

        for _ in range(100):  # wait for the background sync
            if not zoho.sync_running("123"):
                break
            time.sleep(0.05)
        self.assertEqual(zoho.PROGRESS["123"]["state"], "done", zoho.PROGRESS["123"])

        with db.connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM bill_lines ORDER BY bill_date")]
            tok = conn.execute("SELECT access_token, refresh_token, org_name FROM connections").fetchone()
        ids = {r["bill_id"] for r in rows}
        self.assertEqual(ids, {"b1", "b2", "b3", "b4"})  # drafts and bills older than 6 months are excluded
        self.assertEqual(len(rows), 5)
        self.assertNotIn("AT1", tok["access_token"])      # tokens are stored encrypted
        self.assertNotIn("RT1", tok["refresh_token"])
        self.assertEqual(tok["org_name"], "Volta Energy")  # the default organization was chosen

        html = self.c.get("/dashboard").get_data(as_text=True)
        data = json.loads(html.split('<script id="data" type="application/json">')[1].split("</script>")[0])
        self.assertEqual([v["name"] for v in data["vendors"]],
                         ["Guangzhou EnerCell", "Pune PolyMould", "Shenzhen PowerCell"])
        cell = data["products"]["i1|USD"]
        self.assertEqual(cell["cat"], "Raw Materials")
        self.assertEqual(set(cell["vendors"]), {"v1", "v2"})  # both vendors sell the same product

        # disconnect removes data and revokes the token at Zoho
        self.c.post("/disconnect", data={"csrf": self.token("/connect")})
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM bill_lines").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM connections").fetchone()[0], 0)
        self.assertEqual(fake_post.revoked, "RT1")


class AnalysisTest(unittest.TestCase):
    @staticmethod
    def row(d, vendor, rate, qty=1, cur="USD", acct="Raw Materials", item="i1"):
        return dict(bill_date=d, vendor_id=vendor, vendor_name="Vendor " + vendor, currency=cur, item_id=item,
                    item_name="Cell", unit="pcs", rate=rate, quantity=qty, bill_number="x",
                    account_name=acct, product_type="goods")

    def test_vendor_tree_and_stats(self):
        r = self.row
        tree = analysis.build_tree([r("2026-02-01", "A", 36, 100), r("2026-01-01", "A", 30, 100),
                                    r("2026-02-05", "B", 33, 50), r("2026-02-06", "A", 5, 1, item="i2")])
        self.assertEqual([v["name"] for v in tree["vendors"]], ["Vendor A", "Vendor B"])
        a = next(v for v in tree["vendors"] if v["id"] == "A")
        self.assertEqual(len(a["products"]), 2)                      # A's product branches
        s = tree["products"]["i1|USD"]["vendors"]
        self.assertEqual([p[0] for p in s["A"]["purchases"]], ["2026-01-01", "2026-02-01"])  # sorted by date
        self.assertAlmostEqual(s["A"]["avg"], 33.0)                  # quantity-weighted
        self.assertAlmostEqual(s["A"]["change"], 20.0)
        self.assertIsNone(s["B"]["change"])                          # single purchase
        self.assertTrue(s["B"]["best"])
        self.assertAlmostEqual(s["A"]["vs_lowest"], (36 / 33 - 1) * 100)
        self.assertEqual(a["spend"], {"USD": 36 * 100 + 30 * 100 + 5})

    def test_different_currencies_are_not_compared(self):
        r = self.row
        tree = analysis.build_tree([r("2026-01-01", "A", 10), r("2026-01-01", "B", 800, cur="INR")])
        self.assertEqual(set(tree["products"]), {"i1|USD", "i1|INR"})
        for p in tree["products"].values():
            self.assertTrue(all(s["vs_lowest"] is None for s in p["vendors"].values()))

    def test_missing_account_is_uncategorized(self):
        tree = analysis.build_tree([self.row("2026-01-01", "A", 10, acct="")])
        self.assertEqual(tree["products"]["i1|USD"]["cat"], "Uncategorized")


if __name__ == "__main__":
    unittest.main()
