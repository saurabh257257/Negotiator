"""E7: facts and talking points for a component, built from the vendor's purchase history and cost drivers.

All numbers are computed here in code. AI (when configured) only rephrases them.
"""
from datetime import date

from . import ai, catalog, drivers


def build_facts(component: dict, tree: dict, mobile: str, vendor_id: str | None = None, today: str | None = None):
    """Return a facts dict, or {"error": ...} when there is not enough information."""
    today = today or date.today().isoformat()
    tpl = catalog.BY_ID.get(component["template"], catalog.OTHER)
    prod = tree["products"].get(component["item_key"]) if component.get("item_key") else None
    if not prod:
        return {"error": "This component is not matched to anything you buy in Zoho yet. "
                         "Pick the matching Zoho item on the product page."}
    if not tpl["w"]:
        return {"error": "This component has no cost-driver template. Choose a template on the product page."}

    vendors = prod["vendors"]
    vid = vendor_id if vendor_id in vendors else max(vendors, key=lambda v: vendors[v]["latest_date"])
    s = vendors[vid]
    ps = s["purchases"]  # sorted by date: [date, bill, rate, qty]
    d0, r0, d1, r1 = ps[0][0], ps[0][2], ps[-1][0], ps[-1][2]
    cur = prod["cur"]
    vname = next((v["name"] for v in tree["vendors"] if v["id"] == vid), vid)

    rows, wsum, wdata, supported, implied = [], 0.0, 0.0, 0.0, 0.0
    for did, w in tpl["w"].items():
        ser = drivers.series(did, mobile)
        c_inc = drivers.change(ser, d0, d1) if d1 > d0 else None
        c_since = drivers.change(ser, d0, today)
        wsum += w
        if c_since is not None:
            wdata += w
            implied += w * c_since
        if c_inc is not None:
            supported += w * c_inc
        rows.append({"driver": catalog.DRIVERS[did]["name"], "weight": w, "change_at_increase": c_inc,
                     "change_since": c_since, "impact_since": None if c_since is None else w * c_since,
                     "has_data": c_since is not None})

    peers = [{"vendor": next((v["name"] for v in tree["vendors"] if v["id"] == pid), pid),
              "latest_rate": o["latest"], "latest_date": o["latest_date"]}
             for pid, o in vendors.items() if pid != vid]
    justified = r0 * (1 + implied)
    return {
        "component": component["name"], "matched_item": prod["name"], "template": tpl["name"], "currency": cur,
        "vendor": vname, "vendor_id": vid, "vendors_available": [{"id": k, "name": next(
            (v["name"] for v in tree["vendors"] if v["id"] == k), k)} for k in vendors],
        "first_date": d0, "first_rate": r0, "latest_date": d1, "latest_rate": r1, "purchases": len(ps),
        "vendor_change": (r1 / r0 - 1) if r0 and len(ps) > 1 else None,
        "supported_at_increase": supported if d1 > d0 else None,
        "drivers": rows, "coverage": wdata / wsum if wsum else 0.0, "today": today,
        "justified_price": justified if wdata else None,
        "gap": (r1 / justified - 1) if wdata and justified else None,
        "peers": peers,
    }


def _pct(x, d=1):
    return f"{x * 100:+.{d}f}%"


def deterministic_points(f: dict) -> list[str]:
    cur, pts = f["currency"], []
    if f["coverage"] == 0:
        return ["There is no market data yet for the cost drivers behind this component. Refresh market data or upload "
                "benchmarks on the Drivers page, then come back."]
    if f["vendor_change"] is not None:
        pts.append(f"Your rate moved from {cur} {f['first_rate']:,.2f} on {f['first_date']} to {cur} {f['latest_rate']:,.2f} "
                   f"on {f['latest_date']} ({_pct(f['vendor_change'])}).")
        if f["supported_at_increase"] is not None:
            over = f["vendor_change"] - f["supported_at_increase"]
            verdict = ("larger than the cost drivers supported" if over > 0.02 else
                       "broadly in line with the cost drivers" if over > -0.02 else "smaller than the cost drivers supported")
            pts.append(f"Over that same period the cost drivers behind this item supported about "
                       f"{_pct(f['supported_at_increase'])}, so the increase was {verdict}.")
    movers = sorted((r for r in f["drivers"] if r["has_data"]), key=lambda r: -abs(r["impact_since"]))[:3]
    for r in movers:
        pts.append(f"{r['driver']} is {_pct(r['change_since'])} since {f['first_date']}. At {r['weight'] * 100:.0f}% of the "
                   f"price, that moves the cost by about {_pct(r['impact_since'], 2)}.")
    if f["justified_price"] is not None:
        gap = f["gap"]
        if gap > 0.01:
            pts.append(f"Taken together, the drivers point to a price near {cur} {f['justified_price']:,.2f} today. "
                       f"The current rate of {cur} {f['latest_rate']:,.2f} is {_pct(gap)} above that.")
        else:
            pts.append(f"Taken together, the drivers point to a price near {cur} {f['justified_price']:,.2f} today, "
                       f"which is in line with the current rate of {cur} {f['latest_rate']:,.2f}. No reduction is indicated now.")
    cheaper = [p for p in f["peers"] if p["latest_rate"] < f["latest_rate"]]
    if cheaper:
        p = min(cheaper, key=lambda p: p["latest_rate"])
        pts.append(f"{p['vendor']} supplied the same item at {cur} {p['latest_rate']:,.2f} (latest {p['latest_date']}), "
                   f"{(1 - p['latest_rate'] / f['latest_rate']) * 100:.0f}% lower.")
    if f["gap"] is not None and f["gap"] > 0.03:
        pts.append(f"Ask: revise the rate to about {cur} {f['justified_price']:,.2f} from the next order, or share the cost "
                   f"breakdown behind the current rate.")
    if f["coverage"] < 1:
        pts.append(f"Note: market data covers only {f['coverage'] * 100:.0f}% of the cost weights for this item, so the "
                   f"estimate is partial.")
    pts.append("These are public market indicators, not the vendor's contract costs. Vendors often buy inputs weeks ahead, "
               "so ask when a reduction will reach their cost.")
    return pts


def talking_points(f: dict) -> tuple[list[str], str, str | None]:
    """Return (points, source, note). source is 'ai' or 'rules'; note says why AI text was not used."""
    note = None
    if f["coverage"] > 0:
        if ai.configured():
            try:
                return ai.talking_points(f), "ai", None
            except ai.AIError as exc:
                note = str(exc)
        else:
            note = "AI is not configured (no ANTHROPIC_API_KEY)"
    return deterministic_points(f), "rules", note
