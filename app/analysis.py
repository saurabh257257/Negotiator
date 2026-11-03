"""Group stored bill lines into category -> product -> vendor purchase-rate history."""
from markupsafe import Markup

GROUP_BY = {
    "account": "Accounting account",
    "type": "Product type",
    "none": "No grouping",
}


def _category(row, by):
    if by == "account":
        return row["account_name"] or "Uncategorized"
    if by == "type":
        return (row["product_type"] or "Uncategorized").title()
    return "All products"


def build_groups(rows, by="account"):
    cats: dict = {}
    for r in rows:
        prod_key = r["item_id"] or r["item_name"].strip().lower()
        prod = cats.setdefault(_category(r, by), {}).setdefault(
            prod_key, {"name": r["item_name"], "unit": r["unit"], "vendors": {}})
        vkey = (r["vendor_id"] or r["vendor_name"], r["currency"])
        v = prod["vendors"].setdefault(vkey, {"vendor": r["vendor_name"], "currency": r["currency"], "purchases": []})
        v["purchases"].append({"date": r["bill_date"], "bill": r["bill_number"], "rate": r["rate"], "qty": r["quantity"]})

    out = []
    for cat, prods in cats.items():
        products = []
        for prod in prods.values():
            vendors = [_vendor_stats(v) for v in prod["vendors"].values()]
            vendors.sort(key=lambda v: (v["currency"] or "", v["latest_rate"]))
            _mark_spread(vendors)
            products.append({**prod, "vendors": vendors, "n_purchases": sum(v["count"] for v in vendors),
                             "spread": max((v["spread_pct"] or 0 for v in vendors), default=0)})
        products.sort(key=lambda p: p["name"].lower())
        out.append({"name": cat, "products": products, "n_vendors": len({v["vendor"] for p in products for v in p["vendors"]})})
    out.sort(key=lambda c: (c["name"] == "Uncategorized", c["name"].lower()))
    return out


def _vendor_stats(v):
    ps = sorted(v["purchases"], key=lambda p: p["date"])
    rates = [p["rate"] for p in ps]
    qty = sum(p["qty"] for p in ps)
    weighted = sum(p["rate"] * p["qty"] for p in ps) / qty if qty else sum(rates) / len(rates)
    first, latest = rates[0], rates[-1]
    return {
        "vendor": v["vendor"], "currency": v["currency"], "purchases": ps, "count": len(ps),
        "latest_rate": latest, "latest_date": ps[-1]["date"], "first_rate": first,
        "min": min(rates), "max": max(rates), "avg": weighted, "qty": qty,
        "change_pct": (latest / first - 1) * 100 if first and len(ps) > 1 else None,
        "spread_pct": None, "best": False,
    }


def _mark_spread(vendors):
    """When one product is bought from several vendors in the same currency, show how far each
    vendor's latest rate is above the cheapest one. That gap is the negotiation lever."""
    by_cur: dict = {}
    for v in vendors:
        by_cur.setdefault(v["currency"], []).append(v)
    for group in by_cur.values():
        if len(group) < 2:
            continue
        lowest = min(g["latest_rate"] for g in group)
        for g in group:
            g["best"] = g["latest_rate"] == lowest
            g["spread_pct"] = (g["latest_rate"] / lowest - 1) * 100 if lowest else None


def sparkline(values, w=90, h=22):
    if len(values) < 2:
        return Markup("")
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    pts = " ".join(f"{i / (len(values) - 1) * w:.1f},{h - 2 - (v - lo) / span * (h - 4):.1f}"
                   for i, v in enumerate(values))
    return Markup(f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
                  f'<polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>')
