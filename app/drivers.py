"""Cost-driver price series: free Yahoo Finance data where it exists, user-uploaded CSV benchmarks otherwise."""
import csv
import io
from datetime import date, datetime, timezone

import requests

from . import catalog, db

UA = {"User-Agent": "Mozilla/5.0"}


def refresh_yahoo() -> dict:
    """Download ~1 year of daily closes for every driver that has a Yahoo symbol."""
    done, errors = {}, {}
    for did, d in catalog.DRIVERS.items():
        if not d["yahoo"]:
            continue
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{d['yahoo']}",
                             params={"range": "1y", "interval": "1d"}, headers=UA, timeout=30)
            res = r.json()["chart"]["result"][0]
            rows = [(did, "", datetime.fromtimestamp(t, timezone.utc).date().isoformat(), float(c))
                    for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c]
            with db.connect() as conn:
                conn.executemany("INSERT OR REPLACE INTO driver_points (driver,mobile,date,value) VALUES (?,?,?,?)", rows)
            done[did] = len(rows)
        except Exception as exc:
            errors[did] = str(exc)[:120]
    return {"ok": done, "errors": errors}


def parse_csv_points(data: bytes) -> list[tuple[str, float]]:
    """Accepts 'date,value' lines (header optional). Dates must be YYYY-MM-DD."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2:
            continue
        try:
            d = date.fromisoformat(row[0].strip())
            v = float(row[1].replace(",", "").strip())
        except ValueError:
            continue  # header or malformed line
        if v > 0:
            out.append((d.isoformat(), v))
    return out[:20000]


def add_manual(mobile: str, driver: str, data: bytes) -> int:
    if driver not in catalog.DRIVERS or catalog.DRIVERS[driver]["yahoo"]:
        raise ValueError("That driver does not take uploaded benchmarks")
    pts = parse_csv_points(data)
    if not pts:
        raise ValueError("No valid 'date,value' rows found (dates must look like 2026-10-31)")
    with db.connect() as conn:
        conn.executemany("INSERT OR REPLACE INTO driver_points (driver,mobile,date,value) VALUES (?,?,?,?)",
                         [(driver, mobile, d, v) for d, v in pts])
    return len(pts)


def series(driver: str, mobile: str) -> list[tuple[str, float]]:
    owner = "" if catalog.DRIVERS[driver]["yahoo"] else mobile
    with db.connect() as conn:
        rows = conn.execute("SELECT date, value FROM driver_points WHERE driver=? AND mobile=? ORDER BY date",
                            (driver, owner)).fetchall()
    return [(r["date"], r["value"]) for r in rows]


def value_on(ser, day: str):
    """Last known value on or before `day`; falls back to the first value after it."""
    best = None
    for d, v in ser:
        if d <= day:
            best = v
        else:
            return best if best is not None else v
    return best


def change(ser, d_from: str, d_to: str):
    """Fractional change between two dates, or None when data is missing."""
    a, b = value_on(ser, d_from), value_on(ser, d_to)
    if a is None or b is None or a == 0:
        return None
    return b / a - 1


def status(mobile: str) -> list[dict]:
    out = []
    for did, d in catalog.DRIVERS.items():
        ser = series(did, mobile)
        out.append({"id": did, "name": d["name"], "type": d["type"], "yahoo": d["yahoo"], "points": len(ser),
                    "last_date": ser[-1][0] if ser else None, "last_value": ser[-1][1] if ser else None})
    return out
