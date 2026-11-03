"""Products and components (E8/E9), cost drivers (E3-lite) and talking points (E7)."""
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from . import ai, alerts, analysis, catalog, db, drivers, importers, matching, negotiation, saved
from .auth import login_required

bp = Blueprint("work", __name__)


def _tree(mobile):
    with db.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM bill_lines WHERE mobile=?", (mobile,))]
    return analysis.build_tree(rows)


def _product(pid):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=? AND mobile=?", (pid, session["mobile"])).fetchone()
    if not row:
        abort(404)
    return dict(row)


def _component(pid, cid):
    _product(pid)
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM components WHERE id=? AND product_id=? AND mobile=?",
                           (cid, pid, session["mobile"])).fetchone()
    if not row:
        abort(404)
    return dict(row)


def _add_components(pid, mobile, comps, source):
    tree = _tree(mobile)
    rows = []
    for c in comps:
        key, sc = matching.best_match(c["name"], tree["products"])
        rows.append((pid, mobile, c["name"], c["qty"], c.get("unit", ""), c["template"], source, key, sc))
    with db.connect() as conn:
        conn.executemany("INSERT INTO components (product_id,mobile,name,qty,unit,template,source,item_key,match_score) "
                         "VALUES (?,?,?,?,?,?,?,?,?)", rows)
    return len(rows), sum(1 for r in rows if r[7])


def _summary(tree, key):
    """Latest rate for a matched Zoho product: from the vendor bought from most recently."""
    prod = tree["products"].get(key) if key else None
    if not prod:
        return None
    vid = max(prod["vendors"], key=lambda v: prod["vendors"][v]["latest_date"])
    s = prod["vendors"][vid]
    return {"name": prod["name"], "cur": prod["cur"], "latest": s["latest"], "date": s["latest_date"],
            "change": s["change"], "vendor": next((v["name"] for v in tree["vendors"] if v["id"] == vid), vid),
            "n_vendors": len(prod["vendors"])}


# ------------------------------------------------------------------ products
@bp.get("/products")
@login_required
def products():
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM components c WHERE c.product_id=p.id) AS n, "
            "(SELECT COUNT(*) FROM components c WHERE c.product_id=p.id AND c.item_key IS NOT NULL) AS matched "
            "FROM products p WHERE mobile=? ORDER BY id DESC", (session["mobile"],)).fetchall()
    return render_template("products.html", products=[dict(r) for r in rows], ai_on=ai.configured())


@bp.post("/products")
@login_required
def product_create():
    name = request.form.get("name", "").strip()[:120]
    if not name:
        flash("Give the product a name.", "error")
        return redirect(url_for("work.products"))
    with db.connect() as conn:
        cur = conn.execute("INSERT INTO products (mobile,name,description,created_at) VALUES (?,?,?,?)",
                           (session["mobile"], name, request.form.get("description", "").strip()[:1000],
                            datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return redirect(url_for("work.product", pid=cur.lastrowid))


@bp.get("/products/<int:pid>")
@login_required
def product(pid):
    p = _product(pid)
    tree = _tree(session["mobile"])
    with db.connect() as conn:
        comps = [dict(r) for r in conn.execute("SELECT * FROM components WHERE product_id=? ORDER BY id", (pid,))]
    for c in comps:
        c["tracked"] = _summary(tree, c["item_key"])
    items = sorted(((k, f"{v['name']} ({v['cur'] or '-'})") for k, v in tree["products"].items()), key=lambda x: x[1].lower())
    return render_template("product.html", p=p, comps=comps, items=items, templates=catalog.TEMPLATES + [catalog.OTHER],
                           tpl=catalog.BY_ID, ai_on=ai.configured(), has_zoho=bool(tree["products"]))


@bp.post("/products/<int:pid>/delete")
@login_required
def product_delete(pid):
    _product(pid)
    with db.connect() as conn:
        conn.execute("DELETE FROM components WHERE product_id=?", (pid,))
        conn.execute("DELETE FROM products WHERE id=?", (pid,))
    saved.forget_component(session["mobile"], product_id=pid)
    flash("Product deleted.", "ok")
    return redirect(url_for("work.products"))


# ------------------------------------------------------------------ components
@bp.post("/products/<int:pid>/components")
@login_required
def component_add(pid):
    _product(pid)
    name = request.form.get("name", "").strip()[:200]
    if not name:
        flash("Enter a component name.", "error")
        return redirect(url_for("work.product", pid=pid))
    try:
        qty = float(request.form.get("qty") or 1)
    except ValueError:
        qty = 1.0
    _add_components(pid, session["mobile"], [{"name": name, "qty": qty if 0 < qty < 1e7 else 1.0,
                                              "unit": request.form.get("unit", "").strip()[:20],
                                              "template": catalog.classify(name)}], "manual")
    return redirect(url_for("work.product", pid=pid))


@bp.post("/products/<int:pid>/upload")
@login_required
def component_upload(pid):
    _product(pid)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a file to upload.", "error")
        return redirect(url_for("work.product", pid=pid))
    try:
        comps = importers.parse_upload(f.filename, f.read(importers.MAX_BYTES + 1))
    except importers.ImportProblem as exc:
        flash(str(exc), "error")
        return redirect(url_for("work.product", pid=pid))
    n, matched = _add_components(pid, session["mobile"], comps, "upload")
    flash(f"Added {n} components from {f.filename} ({matched} matched to items you buy in Zoho).", "ok")
    return redirect(url_for("work.product", pid=pid))


@bp.post("/products/<int:pid>/generate")
@login_required
def component_generate(pid):
    p = _product(pid)
    if not ai.configured():
        flash("AI is not configured on this server yet (ANTHROPIC_API_KEY). Add components by hand or upload "
              "an Excel/CSV file.", "error")
        return redirect(url_for("work.product", pid=pid))
    try:
        comps = ai.generate_components(p["name"], p["description"] or "")
    except ai.AIError as exc:
        flash(str(exc), "error")
        return redirect(url_for("work.product", pid=pid))
    n, matched = _add_components(pid, session["mobile"], comps, "ai")
    flash(f"AI suggested {n} components ({matched} matched to Zoho). Review them before relying on them.", "ok")
    return redirect(url_for("work.product", pid=pid))


@bp.post("/products/<int:pid>/components/<int:cid>")
@login_required
def component_update(pid, cid):
    _component(pid, cid)
    tpl = request.form.get("template", "other")
    key = request.form.get("item_key") or None
    if tpl not in catalog.BY_ID:
        abort(400)
    if key and key not in _tree(session["mobile"])["products"]:
        abort(400)
    with db.connect() as conn:
        conn.execute("UPDATE components SET template=?, item_key=?, match_score=? WHERE id=?",
                     (tpl, key, 1.0 if key else None, cid))
    return redirect(url_for("work.product", pid=pid))


@bp.post("/products/<int:pid>/components/<int:cid>/delete")
@login_required
def component_delete(pid, cid):
    _component(pid, cid)
    with db.connect() as conn:
        conn.execute("DELETE FROM components WHERE id=?", (cid,))
    saved.forget_component(session["mobile"], cid=cid)
    return redirect(url_for("work.product", pid=pid))


# ------------------------------------------------------------------ talking points (E7)
# Talking points are generated ONLY when the user clicks Generate/Regenerate (a POST), then saved. Opening a page
# (GET) just shows the last saved version, so it costs nothing and is instant.
def _pick(mobile, scope, ref):
    """The saved version asked for with ?id=, otherwise the latest one (or None)."""
    tid = request.args.get("id", type=int)
    if tid:
        rec = saved.get(mobile, tid)
        if not rec or rec["scope"] != scope or rec["ref"] != str(ref):
            abort(404)
        return rec
    return saved.latest(mobile, scope, ref)


def _stale(rec, mobile):
    return bool(rec) and rec["data_stamp"] != saved.stamp(mobile)


@bp.get("/talking-points")
@login_required
def talking_hub():
    """The Talking points tab: every vendor with its last saved brief, plus saved product-level ones."""
    mobile = session["mobile"]
    tree = _tree(mobile)
    gone = alerts.dismissed(mobile)
    per: dict = {}
    for a in alerts.compute(tree, alerts.get_settings(mobile)):
        if a["key"] not in gone:
            per[a["vendor_id"]] = per.get(a["vendor_id"], 0) + 1
    last, now = saved.latest_by_scope(mobile, "vendor"), saved.stamp(mobile)
    rows = [{"id": v["id"], "name": v["name"], "products": len(v["products"]), "spend": v["spend"],
             "alerts": per.get(v["id"], 0), "saved": last.get(v["id"]),
             "stale": bool(last.get(v["id"])) and last[v["id"]]["data_stamp"] != now} for v in tree["vendors"]]
    rows.sort(key=lambda r: (r["saved"] is None, -r["alerts"], r["name"].lower()))
    with db.connect() as conn:
        pnames = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM products WHERE mobile=?", (mobile,))}
    comps = [{**m, "product": pnames.get(m["product_id"], ""), "stale": m["data_stamp"] != now}
             for m in saved.latest_by_scope(mobile, "component").values()]
    comps.sort(key=lambda m: m["created_at"], reverse=True)
    return render_template("talking_hub.html", rows=rows, comps=comps, ai_on=ai.configured())


# --- vendor level: ONE brief for a call covering all the products bought from the vendor
@bp.get("/talking-points/vendor")
@login_required
def vendor_talking_points():
    mobile = session["mobile"]
    vid = request.args.get("vendor", "")
    rec = _pick(mobile, "vendor", vid)
    vendor = next((v for v in _tree(mobile)["vendors"] if v["id"] == vid), None)
    if not vendor and not rec:
        return render_template("vendor_talking.html", rec=None, vid=vid, name=None, n_products=0, error="That vendor was not found in your Zoho purchases.",
                               history=[], stale=False, older=False, ai_on=ai.configured())
    latest = saved.latest(mobile, "vendor", vid)
    return render_template("vendor_talking.html", rec=rec, vid=vid, name=vendor["name"] if vendor else rec["title"],
                           n_products=len(vendor["products"]) if vendor else 0, error=None,
                           history=saved.history(mobile, "vendor", vid), stale=_stale(rec, mobile),
                           older=bool(rec and latest and rec["id"] != latest["id"]), ai_on=ai.configured())


@bp.post("/talking-points/vendor")
@login_required
def vendor_talking_generate():
    mobile = session["mobile"]
    vid = request.form.get("vendor", "")
    vf = negotiation.vendor_facts(_tree(mobile), mobile, vid)
    if "error" in vf:
        flash(vf["error"], "error")
        return redirect(url_for("work.talking_hub"))
    points, source, note = negotiation.vendor_talking_points(vf)
    saved.save(mobile, "vendor", vid, vf["vendor"], points, source, note, vf)
    flash("Talking points generated and saved.", "ok")
    return redirect(url_for("work.vendor_talking_points", vendor=vid))


@bp.get("/vendors")
@login_required
def vendors_page():
    return redirect(url_for("work.talking_hub"))


@bp.get("/vendors/talking-points")
@login_required
def vendors_talking_legacy():
    return redirect(url_for("work.vendor_talking_points", vendor=request.args.get("vendor", "")))


# --- component level: talking points for one product line
@bp.get("/products/<int:pid>/components/<int:cid>/talking-points")
@login_required
def talking_points(pid, cid):
    mobile = session["mobile"]
    p, c = _product(pid), _component(pid, cid)
    rec = _pick(mobile, "component", cid)
    now = negotiation.build_facts(c, _tree(mobile), mobile)  # numbers only (no AI): says whether generation is possible
    latest = saved.latest(mobile, "component", cid)
    return render_template("talking.html", p=p, c=c, rec=rec, error=None if rec else now.get("error"),
                           vendors=now.get("vendors_available", []), history=saved.history(mobile, "component", cid),
                           stale=_stale(rec, mobile), older=bool(rec and latest and rec["id"] != latest["id"]), ai_on=ai.configured())


@bp.post("/products/<int:pid>/components/<int:cid>/talking-points")
@login_required
def talking_points_generate(pid, cid):
    mobile = session["mobile"]
    _product(pid)
    c = _component(pid, cid)
    facts = negotiation.build_facts(c, _tree(mobile), mobile, request.form.get("vendor") or None)
    if "error" in facts:
        flash(facts["error"], "error")
        return redirect(url_for("work.talking_points", pid=pid, cid=cid))
    points, source, note = negotiation.talking_points(facts)
    saved.save(mobile, "component", cid, c["name"], points, source, note, facts, product_id=pid)
    flash("Talking points generated and saved.", "ok")
    return redirect(url_for("work.talking_points", pid=pid, cid=cid))


# ------------------------------------------------------------------ price-gap alerts (E2)
_count_cache: dict = {}


def open_alert_count(mobile: str) -> int:
    """Number of open (not dismissed) alerts, cached until the data or settings change."""
    s = alerts.get_settings(mobile)
    with db.connect() as conn:
        n, last = conn.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM bill_lines WHERE mobile=?", (mobile,)).fetchone()
    gone = alerts.dismissed(mobile)
    key = (mobile, n, last, s["increase_pct"], s["gap_pct"], len(gone))
    if key not in _count_cache:
        if len(_count_cache) > 200:
            _count_cache.clear()
        _count_cache[key] = sum(1 for a in alerts.compute(_tree(mobile), s) if a["key"] not in gone)
    return _count_cache[key]


@bp.get("/alerts")
@login_required
def alerts_page():
    mobile = session["mobile"]
    settings = alerts.get_settings(mobile)
    gone = alerts.dismissed(mobile)
    every = alerts.compute(_tree(mobile), settings)
    open_ = [a for a in every if a["key"] not in gone]
    show_dismissed = request.args.get("show") == "dismissed"
    kind = request.args.get("type") if request.args.get("type") in ("increase", "gap") else "all"
    rows = [a for a in every if (a["key"] in gone) == show_dismissed and kind in ("all", a["type"])]
    extra: dict = {}
    for a in open_:
        extra[a["currency"] or "-"] = extra.get(a["currency"] or "-", 0) + a["extra"]
    with db.connect() as conn:
        comps = conn.execute("SELECT id, product_id, item_key FROM components WHERE mobile=? AND item_key IS NOT NULL",
                             (mobile,)).fetchall()
    talk = {}
    for c in comps:
        talk.setdefault(c["item_key"], (c["product_id"], c["id"]))
    return render_template("alerts.html", alerts=rows, settings=settings, kind=kind, show_dismissed=show_dismissed,
                           n_open=len(open_), n_inc=sum(1 for a in open_ if a["type"] == "increase"),
                           n_gap=sum(1 for a in open_ if a["type"] == "gap"), n_dismissed=len(every) - len(open_),
                           extra=extra, talk=talk)


@bp.post("/alerts/settings")
@login_required
def alerts_settings():
    try:
        alerts.save_settings(session["mobile"], float(request.form.get("increase_pct", "")),
                             float(request.form.get("gap_pct", "")))
        flash("Alert thresholds saved.", "ok")
    except ValueError as exc:
        flash(str(exc) if "between" in str(exc) else "Enter both thresholds as numbers.", "error")
    return redirect(url_for("work.alerts_page"))


@bp.post("/alerts/dismiss")
@login_required
def alerts_dismiss():
    key = request.form.get("key", "")
    if key and request.form.get("undo"):
        alerts.restore(session["mobile"], key)
    elif key:
        alerts.dismiss(session["mobile"], key)
    return redirect(request.referrer or url_for("work.alerts_page"))


# ------------------------------------------------------------------ drivers (E3-lite)
@bp.get("/drivers")
@login_required
def drivers_page():
    return render_template("drivers.html", rows=drivers.status(session["mobile"]))


@bp.post("/drivers/refresh")
@login_required
def drivers_refresh():
    res = drivers.refresh_yahoo()
    if res["errors"]:
        flash("Some market data could not be downloaded: " + ", ".join(res["errors"]), "error")
    if res["ok"]:
        flash(f"Updated {len(res['ok'])} market series from Yahoo Finance.", "ok")
    return redirect(url_for("work.drivers_page"))


@bp.post("/drivers/<driver>/upload")
@login_required
def drivers_upload(driver):
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a CSV file with 'date,value' rows.", "error")
        return redirect(url_for("work.drivers_page"))
    try:
        n = drivers.add_manual(session["mobile"], driver, f.read(2_000_000))
    except ValueError as exc:
        flash(str(exc), "error")
    else:
        flash(f"Saved {n} benchmark points.", "ok")
    return redirect(url_for("work.drivers_page"))
