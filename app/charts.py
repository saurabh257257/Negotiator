"""Small server-side SVG charts."""
from datetime import date

from markupsafe import Markup, escape

COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)"]


def _ord(d: str) -> int:
    return date.fromisoformat(d).toordinal()


def trend_chart(trend: list, price: list, w: int = 560, h: int = 160):
    """Cost-driver price lines and the vendor's price line, all indexed to 100 at the first purchase.

    trend: [{"driver": str, "points": [[date, index], ...], "change": fraction}]
    price: [[date, index], ...] the vendor's rate at each purchase (step line, extended to today)
    """
    lines = [t for t in (trend or []) if len(t.get("points", [])) >= 2]
    if not lines and len(price or []) < 2:
        return Markup("")
    pl, pr, pt, pb = 40, 10, 10, 22
    vals = [v for t in lines for _, v in t["points"]] + [v for _, v in (price or [])] + [100.0]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.15 or 1.0
    lo, hi = lo - pad, hi + pad
    days = [_ord(d) for t in lines for d, _ in t["points"]] + [_ord(d) for d, _ in (price or [])]
    t0, t1 = min(days), max(days)
    span = (t1 - t0) or 1
    X = lambda d: pl + (w - pl - pr) * (_ord(d) - t0) / span
    Y = lambda v: pt + (h - pt - pb) * (1 - (v - lo) / (hi - lo))

    out = [f'<svg class="trend" viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Cost driver price trends against your price">']
    for v in (lo + (hi - lo) * 0.0, 100.0, hi):                      # grid: bottom, the 100 baseline, top
        base = abs(v - 100.0) < 1e-9
        out.append(f'<line x1="{pl}" x2="{w - pr}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="var(--{"muted" if base else "border"})" '
                   f'stroke-width="1" {"stroke-dasharray=\"3 3\"" if base else ""}/>')
        out.append(f'<text x="{pl - 5}" y="{Y(v) + 4:.1f}" text-anchor="end">{v:.0f}</text>')
    first = min(d for t in lines for d, _ in t["points"]) if lines else price[0][0]
    last = max(d for t in lines for d, _ in t["points"]) if lines else price[-1][0]
    out.append(f'<text x="{pl}" y="{h - 6}" text-anchor="start">{escape(first)}</text>')
    out.append(f'<text x="{w - pr}" y="{h - 6}" text-anchor="end">{escape(last)}</text>')

    for i, t in enumerate(lines):
        pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in t["points"])
        chg = t.get("change")
        tip = f'{t["driver"]}: {chg * 100:+.1f}%' if chg is not None else t["driver"]
        out.append(f'<polyline points="{pts}" fill="none" stroke="{COLORS[i % len(COLORS)]}" stroke-width="2" stroke-linejoin="round">'
                   f'<title>{escape(tip)}</title></polyline>')
    if price and len(price) >= 2:                                    # the vendor's price: dashed step line with a dot per purchase
        path = [f"M{X(price[0][0]):.1f} {Y(price[0][1]):.1f}"]
        for (_, pv), (d, v) in zip(price, price[1:]):
            path.append(f"L{X(d):.1f} {Y(pv):.1f} L{X(d):.1f} {Y(v):.1f}")
        out.append(f'<path d="{" ".join(path)}" fill="none" stroke="var(--text)" stroke-width="2.4" stroke-dasharray="6 4"><title>Your price</title></path>')
        for d, v in price[:-1]:
            out.append(f'<circle cx="{X(d):.1f}" cy="{Y(v):.1f}" r="2.6" fill="var(--text)"><title>Purchase {escape(d)}</title></circle>')
    out.append("</svg>")
    return Markup("".join(out))
