"""Saved talking points: generated only on request, stored, and shown later without regenerating."""
import json
from datetime import datetime, timezone

from . import db


def stamp(mobile: str) -> str:
    """Fingerprint of the data a brief is built from: the Zoho bill lines and the cost-driver series."""
    with db.connect() as c:
        n, last = c.execute("SELECT COUNT(*), COALESCE(MAX(id),0) FROM bill_lines WHERE mobile=?", (mobile,)).fetchone()
        d, p = c.execute("SELECT COALESCE(MAX(date),''), COUNT(*) FROM driver_points WHERE mobile IN ('', ?)", (mobile,)).fetchone()
    return f"{n}:{last}:{d}:{p}"


def save(mobile, scope, ref, title, points, source, note, facts, product_id=None) -> int:
    with db.connect() as c:
        cur = c.execute(
            "INSERT INTO talking_points (mobile,scope,ref,product_id,title,source,note,points,facts,data_stamp,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (mobile, scope, str(ref), product_id, title, source, note, json.dumps(points), json.dumps(facts, default=str),
             stamp(mobile), datetime.now(timezone.utc).isoformat(timespec="seconds")))
    return cur.lastrowid


def _row(r):
    d = dict(r)
    d["points"], d["facts"] = json.loads(d["points"]), json.loads(d["facts"])
    return d


def get(mobile, tp_id):
    with db.connect() as c:
        r = c.execute("SELECT * FROM talking_points WHERE id=? AND mobile=?", (tp_id, mobile)).fetchone()
    return _row(r) if r else None


def latest(mobile, scope, ref):
    with db.connect() as c:
        r = c.execute("SELECT * FROM talking_points WHERE mobile=? AND scope=? AND ref=? ORDER BY id DESC LIMIT 1",
                      (mobile, scope, str(ref))).fetchone()
    return _row(r) if r else None


def history(mobile, scope, ref, limit=10):
    with db.connect() as c:
        rows = c.execute("SELECT id, source, created_at FROM talking_points WHERE mobile=? AND scope=? AND ref=? "
                         "ORDER BY id DESC LIMIT ?", (mobile, scope, str(ref), limit)).fetchall()
    return [dict(r) for r in rows]


def latest_by_scope(mobile, scope) -> dict:
    """ref -> metadata (no points/facts) of the most recent save for each ref."""
    with db.connect() as c:
        rows = c.execute(
            "SELECT t.id, t.ref, t.product_id, t.title, t.source, t.created_at, t.data_stamp FROM talking_points t "
            "JOIN (SELECT ref, MAX(id) AS mid FROM talking_points WHERE mobile=? AND scope=? GROUP BY ref) m ON t.id = m.mid",
            (mobile, scope)).fetchall()
    return {r["ref"]: dict(r) for r in rows}


def forget_component(mobile, cid=None, product_id=None):
    with db.connect() as c:
        if cid is not None:
            c.execute("DELETE FROM talking_points WHERE mobile=? AND scope='component' AND ref=?", (mobile, str(cid)))
        if product_id is not None:
            c.execute("DELETE FROM talking_points WHERE mobile=? AND scope='component' AND product_id=?", (mobile, product_id))
