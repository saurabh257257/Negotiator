"""E2: price-gap alerts, computed from the stored Zoho purchase history (plain code, no AI).

Two kinds of alert:
  increase  a vendor's latest rate is higher than its previous purchase rate for the same product
  gap       a vendor's latest rate is higher than the cheapest vendor's latest rate for the same product
A product is an item in one currency, so rates in different currencies are never compared.
"""
from datetime import datetime, timezone

from . import db

DEFAULTS = {"increase_pct": 3.0, "gap_pct": 5.0}
LIMITS = (0.1, 100.0)


def get_settings(mobile: str) -> dict:
    with db.connect() as conn:
        row = conn.execute("SELECT increase_pct, gap_pct FROM alert_settings WHERE mobile=?", (mobile,)).fetchone()
    return dict(row) if row else dict(DEFAULTS)


def save_settings(mobile: str, increase_pct: float, gap_pct: float):
    ok = lambda v: LIMITS[0] <= v <= LIMITS[1]
    if not (ok(increase_pct) and ok(gap_pct)):
        raise ValueError(f"Thresholds must be between {LIMITS[0]} and {LIMITS[1]} percent")
    with db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO alert_settings (mobile, increase_pct, gap_pct) VALUES (?,?,?)",
                     (mobile, increase_pct, gap_pct))


def dismissed(mobile: str) -> set:
    with db.connect() as conn:
        return {r["key"] for r in conn.execute("SELECT key FROM alert_dismissed WHERE mobile=?", (mobile,))}


def dismiss(mobile: str, key: str):
    with db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO alert_dismissed (mobile,key,dismissed_at) VALUES (?,?,?)",
                     (mobile, key[:300], datetime.now(timezone.utc).isoformat(timespec="seconds")))


def restore(mobile: str, key: str):
    with db.connect() as conn:
        conn.execute("DELETE FROM alert_dismissed WHERE mobile=? AND key=?", (mobile, key))


def compute(tree: dict, settings: dict) -> list[dict]:
    names = {v["id"]: v["name"] for v in tree["vendors"]}
    out = []
    for pkey, prod in tree["products"].items():
        vendors = prod["vendors"]
        for vid, s in vendors.items():
            ps = s["purchases"]  # sorted by date: [date, bill, rate, qty]
            last = ps[-1][0]
            qty_latest = sum(p[3] for p in ps if p[0] == last)
            earlier = [p for p in ps if p[0] < last]
            if earlier and earlier[-1][2] > 0:
                prev = earlier[-1]
                pct = (s["latest"] / prev[2] - 1) * 100
                if pct >= settings["increase_pct"]:
                    out.append(_alert("increase", pkey, prod, vid, names[vid], pct, settings["increase_pct"], prev[2],
                                      s["latest"], last, qty_latest,
                                      f"Rate rose from {prev[2]:,.2f} on {prev[0]} to {s['latest']:,.2f} on {last}."))
        if len(vendors) > 1:
            low_id = min(vendors, key=lambda v: vendors[v]["latest"])
            low = vendors[low_id]
            if low["latest"] > 0:
                for vid, s in vendors.items():
                    if vid == low_id:
                        continue
                    pct = (s["latest"] / low["latest"] - 1) * 100
                    if pct >= settings["gap_pct"]:
                        last = s["purchases"][-1][0]
                        qty_latest = sum(p[3] for p in s["purchases"] if p[0] == last)
                        a = _alert("gap", pkey, prod, vid, names[vid], pct, settings["gap_pct"], low["latest"], s["latest"],
                                   last, qty_latest,
                                   f"{names[low_id]} supplied the same item at {low['latest']:,.2f} (latest {low['latest_date']}); "
                                   f"{names[vid]} charged {s['latest']:,.2f} on {last}.")
                        a["ref_vendor"] = names[low_id]
                        out.append(a)
    out.sort(key=lambda a: (a["severity"] != "high", -a["pct"]))
    return out


def _alert(kind, pkey, prod, vid, vname, pct, threshold, ref_rate, rate, date, qty, detail):
    return {
        "key": f"{kind}|{pkey}|{vid}|{date}", "type": kind, "product_key": pkey, "product": prod["name"],
        "currency": prod["cur"], "vendor_id": vid, "vendor": vname, "pct": pct, "ref_rate": ref_rate, "rate": rate,
        "date": date, "qty": qty, "extra": max(0.0, (rate - ref_rate) * qty), "detail": detail,
        "severity": "high" if pct >= 2 * threshold else "medium",
    }
