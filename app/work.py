"""Products and components (E8/E9), cost drivers (E3-lite) and talking points (E7)."""
from datetime import datetime, timezone

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from . import ai, analysis, catalog, db, drivers, importers, matching, negotiation
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
    return redirect(url_for("work.product", pid=pid))


# ------------------------------------------------------------------ talking points (E7)
@bp.get("/products/<int:pid>/components/<int:cid>/talking-points")
@login_required
def talking_points(pid, cid):
    p, c = _product(pid), _component(pid, cid)
    facts = negotiation.build_facts(c, _tree(session["mobile"]), session["mobile"], request.args.get("vendor"))
    if "error" in facts:
        return render_template("talking.html", p=p, c=c, facts=None, error=facts["error"], points=[], source=None)
    points, source = negotiation.talking_points(facts)
    return render_template("talking.html", p=p, c=c, facts=facts, error=None, points=points, source=source)


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
