"""Turn stored bill lines into a vendor -> product tree with purchase-rate history."""


def _stats(purchases):
    ps = sorted(purchases, key=lambda p: p[0])  # [date, bill, rate, qty]
    rates = [p[2] for p in ps]
    qty = sum(p[3] for p in ps)
    avg = sum(p[2] * p[3] for p in ps) / qty if qty else sum(rates) / len(rates)
    return {
        "count": len(ps), "latest": rates[-1], "latest_date": ps[-1][0], "avg": avg,
        "min": min(rates), "max": max(rates),
        "change": (rates[-1] / rates[0] - 1) * 100 if rates[0] and len(ps) > 1 else None,
        "purchases": ps,
    }


def build_tree(rows):
    """Return {"vendors": [...], "products": {...}}.

    A product is keyed by item and currency, so rates in different currencies are never compared.
    products[key]["vendors"][vendor_id] holds that vendor's stats for the product, which is what lets
    the UI show every vendor's rate side by side for the same product.
    """
    products: dict = {}
    vendors: dict = {}
    for r in rows:
        pkey = f"{r['item_id'] or r['item_name'].strip().lower()}|{r['currency'] or ''}"
        vid = r["vendor_id"] or r["vendor_name"] or "unknown"
        prod = products.setdefault(pkey, {
            "name": r["item_name"], "unit": r["unit"] or "", "cat": r["account_name"] or "Uncategorized",
            "cur": r["currency"] or "", "raw": {}})
        prod["raw"].setdefault(vid, []).append([r["bill_date"], r["bill_number"] or "", r["rate"], r["quantity"] or 0])
        v = vendors.setdefault(vid, {"id": vid, "name": r["vendor_name"] or "Unknown vendor",
                                     "products": set(), "spend": {}, "purchases": 0})
        v["products"].add(pkey)
        v["purchases"] += 1
        cur = r["currency"] or ""
        v["spend"][cur] = v["spend"].get(cur, 0) + r["rate"] * (r["quantity"] or 0)

    for prod in products.values():
        stats = {vid: _stats(ps) for vid, ps in prod.pop("raw").items()}
        lowest = min(s["latest"] for s in stats.values())
        for s in stats.values():
            multi = len(stats) > 1
            s["best"] = multi and s["latest"] == lowest
            s["vs_lowest"] = (s["latest"] / lowest - 1) * 100 if multi and lowest else None
        prod["vendors"] = stats

    out_vendors = []
    for v in vendors.values():
        v["products"] = sorted(v["products"], key=lambda k: products[k]["name"].lower())
        out_vendors.append(v)
    out_vendors.sort(key=lambda v: v["name"].lower())
    return {"vendors": out_vendors, "products": products}
