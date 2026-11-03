"""Tests for price-gap alerts (E2). No network."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("NEGOTIATOR_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("ZOHO_CLIENT_ID", "test-client")
os.environ.setdefault("ZOHO_CLIENT_SECRET", "test-secret")
os.environ.setdefault("ZOHO_MIN_INTERVAL", "0")
os.environ.setdefault("AUTH_DEMO", "1")
os.environ.setdefault("DEMO_MOBILE", "123")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import alerts, analysis, config, create_app, db  # noqa: E402


def wipe():
    assert config.DB_PATH.parent != config.ROOT / "data", "tests must use a temporary NEGOTIATOR_DATA_DIR"
    db.init()
    with db.connect() as c:
        for t in ("components", "products", "bill_lines", "driver_points", "connections", "alert_settings", "alert_dismissed"):
            c.execute(f"DELETE FROM {t}")


def line(bill, date, vid, vname, item, name, rate, qty, cur="USD", mobile="123"):
    return (mobile, bill, "BN-" + bill, date, "paid", vid, vname, cur, item, name, "", "Raw Materials", "goods", "pcs", rate, qty)


def seed(rows):
    with db.connect() as c:
        c.executemany("INSERT INTO bill_lines (mobile,bill_id,bill_number,bill_date,status,vendor_id,vendor_name,currency,"
                      "item_id,item_name,sku,account_name,product_type,unit,rate,quantity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def tree_of(rows):
    cols = ("mobile bill_id bill_number bill_date status vendor_id vendor_name currency item_id item_name sku account_name "
            "product_type unit rate quantity").split()
    return analysis.build_tree([dict(zip(cols, r)) for r in rows])


S = {"increase_pct": 3.0, "gap_pct": 5.0}
CELL = "LFP cell"


class ComputeTest(unittest.TestCase):
    def rows(self):
        return [
            line("b1", "2026-08-01", "A", "Alpha", "i1", CELL, 100.0, 40),
            line("b2", "2026-10-01", "A", "Alpha", "i1", CELL, 104.0, 30),      # +4% vs previous
            line("b2x", "2026-10-01", "A", "Alpha", "i1", CELL, 104.0, 20),     # same day: quantities add up (50)
            line("b3", "2026-10-05", "B", "Beta", "i1", CELL, 99.0, 10),        # cheapest vendor; one purchase only
            line("b4", "2026-10-02", "C", "Gamma", "i1", CELL, 8000.0, 5, cur="INR"),  # other currency: separate product
        ]

    def test_increase_and_gap_alerts(self):
        out = alerts.compute(tree_of(self.rows()), S)
        kinds = sorted((a["type"], a["vendor"]) for a in out)
        self.assertEqual(kinds, [("gap", "Alpha"), ("increase", "Alpha")])   # Beta (cheapest) and Gamma are not flagged
        inc = next(a for a in out if a["type"] == "increase")
        self.assertAlmostEqual(inc["pct"], 4.0)
        self.assertAlmostEqual(inc["extra"], (104 - 100) * 50)               # extra paid on the latest order (50 units)
        self.assertEqual(inc["severity"], "medium")                          # 4% < 2 x 3%
        gap = next(a for a in out if a["type"] == "gap")
        self.assertAlmostEqual(gap["pct"], (104 / 99 - 1) * 100)
        self.assertEqual(gap["ref_vendor"], "Beta")
        self.assertAlmostEqual(gap["extra"], (104 - 99) * 50)

    def test_thresholds_and_severity(self):
        t = tree_of(self.rows())
        self.assertEqual(alerts.compute(t, {"increase_pct": 5.0, "gap_pct": 6.0}), [])       # raised above both
        high = alerts.compute(t, {"increase_pct": 1.5, "gap_pct": 5.0})
        self.assertEqual(next(a for a in high if a["type"] == "increase")["severity"], "high")  # 4% >= 2 x 1.5%
        self.assertEqual(high[0]["severity"], "high")                                          # high ones sort first

    def test_no_alerts_without_history_or_for_price_drops(self):
        self.assertEqual(alerts.compute(tree_of([line("b1", "2026-10-01", "A", "Alpha", "i1", CELL, 100.0, 1)]), S), [])
        drop = [line("b1", "2026-08-01", "A", "Alpha", "i1", CELL, 100.0, 1), line("b2", "2026-10-01", "A", "Alpha", "i1", CELL, 90.0, 1)]
        self.assertEqual(alerts.compute(tree_of(drop), S), [])

    def test_equal_rates_are_not_a_gap(self):
        rows = [line("b1", "2026-10-01", "A", "Alpha", "i1", CELL, 100.0, 1), line("b2", "2026-10-02", "B", "Beta", "i1", CELL, 100.0, 1)]
        self.assertEqual(alerts.compute(tree_of(rows), S), [])


class SettingsAndRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(); cls.app.testing = True

    def setUp(self):
        wipe()
        seed(ComputeTest().rows())
        self.c = self.app.test_client()
        html = self.c.get("/login").get_data(as_text=True)
        self.t = html.split('name="csrf" value="')[1].split('"')[0]
        self.c.post("/login", data={"mobile": "123", "csrf": self.t})

    def post(self, url, **d):
        return self.c.post(url, data={"csrf": self.t, **d}, follow_redirects=True).get_data(as_text=True)

    def test_page_lists_alerts_and_nav_badge(self):
        html = self.c.get("/alerts").get_data(as_text=True)
        self.assertIn("Rate increase", html)
        self.assertIn("Vendor gap", html)
        self.assertIn("Alpha", html)
        self.assertIn('<span class="badge">2</span>', html)           # two open alerts in the nav
        self.assertIn("USD 450.00", html)                              # (4 + 5) x 50 units, summed per currency
        self.assertEqual(self.c.get("/alerts?type=gap").get_data(as_text=True).count("Vendor gap</b>"), 1)

    def test_login_required(self):
        self.assertEqual(self.app.test_client().get("/alerts").status_code, 302)

    def test_settings_validation_and_effect(self):
        self.assertIn("between", self.post("/alerts/settings", increase_pct="0", gap_pct="5"))
        self.assertIn("between", self.post("/alerts/settings", increase_pct="5", gap_pct="101"))
        self.assertIn("numbers", self.post("/alerts/settings", increase_pct="abc", gap_pct="5"))
        self.assertEqual(alerts.get_settings("123"), {"increase_pct": 3.0, "gap_pct": 5.0})   # nothing saved yet
        html = self.post("/alerts/settings", increase_pct="10", gap_pct="10")
        self.assertIn("thresholds saved", html)
        self.assertIn("No open alerts", html)                          # nothing is above 10%
        self.assertNotIn('class="badge"', html)

    def test_dismiss_and_restore(self):
        out = alerts.compute(tree_of(ComputeTest().rows()), S)
        key = next(a["key"] for a in out if a["type"] == "increase")
        self.post("/alerts/dismiss", key=key)
        self.assertEqual(alerts.dismissed("123"), {key})
        self.assertIn('<span class="badge">1</span>', self.c.get("/alerts").get_data(as_text=True))   # badge refreshed
        shown = self.c.get("/alerts?show=dismissed").get_data(as_text=True)
        self.assertIn("Restore", shown)
        self.post("/alerts/dismiss", key=key, undo="1")
        self.assertEqual(alerts.dismissed("123"), set())
        self.assertEqual(alerts.dismissed("999"), set())               # dismissals are per user

    def test_talking_points_link_when_a_component_is_matched(self):
        with db.connect() as c:
            pid = c.execute("INSERT INTO products (mobile,name,created_at) VALUES ('123','Pack','x')").lastrowid
            c.execute("INSERT INTO components (product_id,mobile,name,qty,template,source,item_key) VALUES (?,?,?,?,?,?,?)",
                      (pid, "123", "LFP cell", 1, "cell", "manual", "i1|USD"))
        self.assertIn("/talking-points?vendor=A", self.c.get("/alerts").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
