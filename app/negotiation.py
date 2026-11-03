"""E7: facts and talking points built from purchase history and cost drivers.

Two levels:
  component  talking points for one product (needs a component matched to a Zoho item)
  vendor     ONE consolidated brief for a call with a vendor, covering all the products bought from them

All numbers are computed here in code. AI (when configured) only rephrases them.
"""
from datetime import date

from . import ai, alerts, catalog, db, drivers


def _name(tree, vid):
    return next((v["name"] for v in tree["vendors"] if v["id"] == vid), vid)


def item_facts(pkey: str, vid: str, template_id: str, tree: dict, mobile: str, today: str | None = None) -> dict:
    """Facts for one product bought from one vendor. coverage is 0 and justified_price None when there is no template or data."""
    today = today or date.today().isoformat()
    tpl = catalog.BY_ID.get(template_id, catalog.OTHER)
    prod = tree["products"][pkey]
    vendors = prod["vendors"]
    s = vendors[vid]
    ps = s["purchases"]  # sorted by date: [date, bill, rate, qty]
    d0, r0, d1, r1 = ps[0][0], ps[0][2], ps[-1][0], ps[-1][2]
    cur = prod["cur"]

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

    peers = [{"vendor": _name(tree, pid), "latest_rate": o["latest"], "latest_date": o["latest_date"]}
             for pid, o in vendors.items() if pid != vid]
    justified = r0 * (1 + implied) if wdata else None
    qty_latest = sum(p[3] for p in ps if p[0] == d1)
    return {
        "component": prod["name"], "matched_item": prod["name"], "product_key": pkey, "template": tpl["name"],
        "template_id": tpl["id"], "currency": cur, "vendor": _name(tree, vid), "vendor_id": vid,
        "vendors_available": [{"id": k, "name": _name(tree, k)} for k in vendors],
        "first_date": d0, "first_rate": r0, "latest_date": d1, "latest_rate": r1, "purchases": len(ps),
        "vendor_change": (r1 / r0 - 1) if r0 and len(ps) > 1 else None,
        "supported_at_increase": supported if d1 > d0 and wdata else None,
        "drivers": rows, "coverage": wdata / wsum if wsum else 0.0, "today": today,
        "justified_price": justified,
        "gap": (r1 / justified - 1) if justified else None,
        "peers": peers, "qty_latest": qty_latest, "spend": sum(p[2] * p[3] for p in ps),
        "extra": max(0.0, (r1 - justified) * qty_latest) if justified else 0.0,
    }


def build_facts(component: dict, tree: dict, mobile: str, vendor_id: str | None = None, today: str | None = None):
    """Component-level facts, or {"error": ...} when there is not enough information."""
    tpl = catalog.BY_ID.get(component["template"], catalog.OTHER)
    prod = tree["products"].get(component["item_key"]) if component.get("item_key") else None
    if not prod:
        return {"error": "This component is not matched to anything you buy in Zoho yet. "
                         "Pick the matching Zoho item on the product page."}
    if not tpl["w"]:
        return {"error": "This component has no cost-driver template. Choose a template on the product page."}
    vendors = prod["vendors"]
    vid = vendor_id if vendor_id in vendors else max(vendors, key=lambda v: vendors[v]["latest_date"])
    facts = item_facts(component["item_key"], vid, component["template"], tree, mobile, today)
    facts["component"] = component["name"]
    return facts


# ------------------------------------------------------------------ vendor level (one call, all products)
def vendor_facts(tree: dict, mobile: str, vendor_id: str, today: str | None = None) -> dict:
    vendor = next((v for v in tree["vendors"] if v["id"] == vendor_id), None)
    if not vendor:
        return {"error": "That vendor was not found in your Zoho purchases."}
    with db.connect() as conn:  # a template the user set on a matched component wins over the name-based guess
        override = {r["item_key"]: r["template"] for r in conn.execute(
            "SELECT item_key, template FROM components WHERE mobile=? AND item_key IS NOT NULL", (mobile,))}
    st = alerts.get_settings(mobile)
    items = []
    for pkey in vendor["products"]:
        tpl = override.get(pkey) or catalog.classify(tree["products"][pkey]["name"])
        items.append(item_facts(pkey, vendor_id, tpl, tree, mobile, today))
    items.sort(key=lambda f: -f["spend"])

    by_cur: dict = {}
    for f in items:
        by_cur.setdefault(f["currency"], []).append(f)
    currencies = {}
    for cur, fs in by_cur.items():
        spend = sum(f["spend"] for f in fs)
        w = lambda f: f["spend"] if spend > 0 else 1.0
        with_data = [f for f in fs if f["justified_price"] is not None]
        wd = sum(w(f) for f in with_data)
        currencies[cur] = {
            "spend": spend, "items": len(fs), "with_data": len(with_data),
            "data_share": (wd / sum(w(f) for f in fs)) if fs else 0.0,
            "weighted_gap": (sum(w(f) * f["gap"] for f in with_data) / wd) if wd else None,
            "extra": sum(f["extra"] for f in with_data),
        }
    primary = max(by_cur, key=lambda c: len(by_cur[c]))
    main = max(by_cur[primary], key=lambda f: f["spend"])
    total_primary = currencies[primary]["spend"]

    for f in items:
        f["spend_share"] = (f["spend"] / currencies[f["currency"]]["spend"]) if currencies[f["currency"]]["spend"] else None
        cheaper = [p for p in f["peers"] if p["latest_rate"] > 0 and (f["latest_rate"] / p["latest_rate"] - 1) * 100 >= st["gap_pct"]]
        f["cheaper_peer"] = min(cheaper, key=lambda p: p["latest_rate"]) if cheaper else None
        f["raised"] = f["vendor_change"] is not None and f["vendor_change"] * 100 >= st["increase_pct"]
        # A gap smaller than the user's rate-increase threshold is within the noise of an estimate, so it is not raised.
        f["concern"] = bool((f["gap"] is not None and f["gap"] * 100 >= st["increase_pct"]) or f["raised"] or f["cheaper_peer"])
    concerns = sorted((f for f in items if f["concern"]), key=lambda f: (-f["extra"], -(f["gap"] or 0), -(f["vendor_change"] or 0)))
    for f in concerns:  # past trends of the cost drivers, shown under each product to raise
        f["trend"], f["price_index"] = trend_data(f, tree, mobile)
    return {"vendor": vendor["name"], "vendor_id": vendor_id, "today": today or date.today().isoformat(),
            "n_products": len(items), "currencies": currencies, "primary_currency": primary,
            "main": main, "main_share": (main["spend"] / total_primary) if total_primary else None,
            "items": items, "concerns": concerns, "settings": st}


def _downsample(pts: list, n: int = 60) -> list:
    if len(pts) <= n:
        return pts
    step = len(pts) / n
    return [pts[int(i * step)] for i in range(n)] + [pts[-1]]


def trend_data(f: dict, tree: dict, mobile: str) -> tuple[list, list]:
    """Each cost driver's price and the vendor's price since the first purchase, all indexed to 100 on that day."""
    start, end = f["first_date"], f["today"]
    trend = []
    for did, w in catalog.BY_ID.get(f["template_id"], catalog.OTHER)["w"].items():
        ser = drivers.series(did, mobile)
        base = drivers.value_on(ser, start)
        if not base:
            continue
        pts = [[start, 100.0]] + [[d, v / base * 100] for d, v in ser if start < d <= end]
        if len(pts) >= 2:
            trend.append({"driver": catalog.DRIVERS[did]["name"], "weight": w, "points": _downsample(pts),
                          "change": pts[-1][1] / 100 - 1})
    by_date = {}
    for d, _bill, rate, _qty in tree["products"][f["product_key"]]["vendors"][f["vendor_id"]]["purchases"]:
        by_date[d] = rate / f["first_rate"] * 100 if f["first_rate"] else 100.0
    price = [[d, v] for d, v in sorted(by_date.items())]
    if price and price[-1][0] < end:
        price.append([end, price[-1][1]])
    return trend, price


def ai_payload(vf: dict) -> dict:
    """A compact, number-only version of the vendor facts for the AI (no free text from the user's data)."""
    def slim(f):
        movers = sorted((r for r in f["drivers"] if r["has_data"]), key=lambda r: -abs(r["impact_since"]))[:3]
        return {"product": f["component"], "currency": f["currency"], "first_rate": f["first_rate"], "first_date": f["first_date"],
                "latest_rate": f["latest_rate"], "latest_date": f["latest_date"], "vendor_change": f["vendor_change"],
                "justified_price": f["justified_price"], "gap": f["gap"], "extra_on_latest_order": f["extra"],
                "share_of_spend": f.get("spend_share"), "market_data_coverage": f["coverage"],
                "top_drivers": [{"driver": r["driver"], "change_since_first_purchase": r["change_since"], "weight": r["weight"]} for r in movers],
                "cheaper_vendor": f["cheaper_peer"]}
    return {"vendor": vf["vendor"], "products_bought": vf["n_products"], "currencies": vf["currencies"],
            "main_area": slim(vf["main"]), "products": [slim(f) for f in vf["items"][:12]]}


def _pct(x, d=1):
    return f"{x * 100:+.{d}f}%"


def vendor_points(vf: dict) -> list[str]:
    """Deterministic talking points for ONE call about all the vendor's products."""
    pc = vf["primary_currency"]
    c = vf["currencies"][pc]
    main = vf["main"]
    items = vf["items"]
    pts = []
    spend_txt = f" (about {pc} {c['spend']:,.0f})" if c["spend"] else ""
    pts.append(f"I'd like to go through all {vf['n_products']} product{'s' if vf['n_products'] != 1 else ''} we bought from you in the last "
               f"6 months{spend_txt} in one conversation, rather than product by product. "
               + (f"The biggest area is {main['component']} ({vf['main_share'] * 100:.0f}% of our spend)." if vf["main_share"] else ""))

    if c["weighted_gap"] is not None:
        g = c["weighted_gap"]
        if g >= 0.01:
            pts.append(f"Across the {c['with_data']} product{'s' if c['with_data'] != 1 else ''} where we have market data "
                       f"({c['data_share'] * 100:.0f}% of our spend), the cost drivers behind your inputs point to prices about {g * 100:.1f}% "
                       f"below what we pay today. On our latest orders that is roughly {pc} {c['extra']:,.0f}.")
        elif g > -0.01:
            pts.append(f"Across the products where we have market data ({c['data_share'] * 100:.0f}% of our spend), your rates are in line "
                       f"with the cost drivers. My aim is to keep it that way and review regularly.")
        else:
            pts.append(f"Across the products where we have market data, the cost drivers actually point slightly higher than our current "
                       f"rates ({_pct(-g)}), so we are not asking for a reduction on those.")

    if main["justified_price"] is not None:
        if main["vendor_change"] is not None and abs(main["vendor_change"]) >= 0.0005:
            pts.append(f"On {main['component']}, the largest area, your rate moved from {pc} {main['first_rate']:,.2f} to "
                       f"{pc} {main['latest_rate']:,.2f} ({_pct(main['vendor_change'])}), while the drivers point to about "
                       f"{pc} {main['justified_price']:,.2f} today ({_pct(main['gap'])} gap).")
        elif main["vendor_change"] is not None:
            pts.append(f"On {main['component']}, the largest area, your rate has been steady at {pc} {main['latest_rate']:,.2f} "
                       f"since {main['first_date']}; the drivers point to about {pc} {main['justified_price']:,.2f} today "
                       f"({_pct(main['gap'])} gap).")
        else:
            pts.append(f"On {main['component']}, the largest area, we paid {pc} {main['latest_rate']:,.2f} on {main['latest_date']}; the drivers "
                       f"point to about {pc} {main['justified_price']:,.2f} today ({_pct(main['gap'])} gap).")
    else:
        pts.append(f"{main['component']} is our biggest area but we have no market data for its cost drivers yet, so I'd like to hear "
                   f"how your cost for it has moved.")
    if main["cheaper_peer"]:
        p = main["cheaper_peer"]
        pts.append(f"For {main['component']}, {p['vendor']} supplied the same item at {pc} {p['latest_rate']:,.2f} "
                   f"(latest {p['latest_date']}), {(1 - p['latest_rate'] / main['latest_rate']) * 100:.0f}% lower than your {pc} "
                   f"{main['latest_rate']:,.2f}.")

    others = [f for f in vf["concerns"] if f is not main][:4]
    for f in others:
        bits = []
        if f["justified_price"] is not None and f["gap"] is not None and f["gap"] * 100 >= vf["settings"]["increase_pct"]:
            bits.append(f"paid {f['currency']} {f['latest_rate']:,.2f} vs about {f['justified_price']:,.2f} implied ({_pct(f['gap'])})")
        if f["raised"]:
            bits.append(f"rate rose {_pct(f['vendor_change'])} since {f['first_date']}")
        if f["cheaper_peer"]:
            p = f["cheaper_peer"]
            bits.append(f"{p['vendor']} supplied it at {f['currency']} {p['latest_rate']:,.2f}")
        pts.append(f"{f['component']}: " + "; ".join(bits) + ".")

    fine = [f["component"] for f in items if not f["concern"] and f["justified_price"] is not None][:3]
    if fine:
        pts.append(f"On {', '.join(fine)} the current rates look in line with the cost drivers, so I'm not raising those.")
    nodata = [f["component"] for f in items if f["justified_price"] is None][:4]
    if nodata:
        pts.append(f"For {', '.join(nodata)} we have no market data, so please share the cost breakdown and we can review them together.")

    if c["extra"] > 0:
        pts.append(f"My ask: a review of these rates as one package, with a revised price list close to the indicated levels from the next "
                   f"order cycle (worth about {pc} {c['extra']:,.0f} on our latest orders), or the cost breakdown behind the current rates.")
    else:
        pts.append("My ask: keep rates transparent and agree to review them regularly against the main cost drivers, in either direction.")
    pts.append("These are public market indicators, not your contract costs. Vendors often buy inputs weeks ahead, so please tell me when "
               "any reduction will reach your cost.")
    return pts


def vendor_talking_points(vf: dict) -> tuple[list[str], str, str | None]:
    """Return (points, source, note) for the vendor call."""
    note = None
    if any(f["coverage"] > 0 for f in vf["items"]):
        if ai.configured():
            try:
                return ai.vendor_talking_points(ai_payload(vf)), "ai", None
            except ai.AIError as exc:
                note = str(exc)
        else:
            note = "AI is not configured (no ANTHROPIC_API_KEY)"
    return vendor_points(vf), "rules", note


# ------------------------------------------------------------------ component level
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
