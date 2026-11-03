"""Tests for the vendor-level call brief: ONE brief covering all the products bought from a vendor. No network."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("NEGOTIATOR_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("ZOHO_CLIENT_ID", "test-client")
os.environ.setdefault("ZOHO_CLIENT_SECRET", "test-secret")
os.environ.setdefault("ZOHO_MIN_INTERVAL", "0")
os.environ.setdefault("AUTH_DEMO", "1")
os.environ.setdefault("DEMO_MOBILE", "123")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ai, analysis, config, create_app, db, negotiation  # noqa: E402

COLS = ("mobile bill_id bill_number bill_date status vendor_id vendor_name currency item_id item_name sku account_name "
        "product_type unit rate quantity").split()


def wipe():
    assert config.DB_PATH.parent != config.ROOT / "data", "tests must use a temporary NEGOTIATOR_DATA_DIR"
    db.init()
    with db.connect() as c:
        for t in ("components", "products", "bill_lines", "driver_points", "connections", "alert_settings", "alert_dismissed",
                  "talking_points"):
            c.execute(f"DELETE FROM {t}")


def line(bill, date, vid, vname, item, name, rate, qty, cur="USD"):
    return ("123", bill, "BN-" + bill, date, "paid", vid, vname, cur, item, name, "", "Raw Materials", "goods", "pcs", rate, qty)


def seed(rows):
    with db.connect() as c:
        c.executemany("INSERT INTO bill_lines (mobile,bill_id,bill_number,bill_date,status,vendor_id,vendor_name,currency,"
                      "item_id,item_name,sku,account_name,product_type,unit,rate,quantity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def driver(did, pts, mobile=""):
    with db.connect() as c:
        c.executemany("INSERT OR REPLACE INTO driver_points (driver,mobile,date,value) VALUES (?,?,?,?)", [(did, mobile, d, v) for d, v in pts])


def tree():
    with db.connect() as c:
        return analysis.build_tree([dict(r) for r in c.execute("SELECT * FROM bill_lines WHERE mobile='123'")])


TODAY = "2026-10-20"


def alpha_rows():
    return [
        line("b1", "2026-06-01", "A", "Alpha", "p1", "LFP cell", 100.0, 50),
        line("b2", "2026-10-01", "A", "Alpha", "p1", "LFP cell", 104.0, 50),          # main area, drivers fell
        line("b3", "2026-10-02", "A", "Alpha", "p2", "Mystery widget", 5.0, 10),      # no template -> no market data
        line("b4", "2026-10-03", "A", "Alpha", "p3", "Copper busbar 3x20", 10.0, 100),  # copper flat -> in line
        line("b5", "2026-10-05", "B", "Beta", "p1", "LFP cell", 99.0, 10),            # cheaper vendor for the same cell
        line("b6", "2026-10-06", "A", "Alpha", "p4", "Cell holder", 800.0, 5, cur="INR"),  # a second currency
    ]


def alpha_drivers():
    driver("lith", [("2026-06-01", 100), ("2026-10-20", 80)], "123")        # lithium -20%
    driver("copper", [("2026-06-01", 100), ("2026-10-20", 101)])            # copper +1%


class VendorFactsTest(unittest.TestCase):
    def setUp(self):
        wipe(); seed(alpha_rows()); alpha_drivers()

    def test_one_brief_covers_all_products_and_keeps_currencies_apart(self):
        vf = negotiation.vendor_facts(tree(), "123", "A", today=TODAY)
        self.assertEqual(vf["n_products"], 4)
        self.assertEqual([f["component"] for f in vf["items"]][:1], ["LFP cell"])          # largest spend first
        self.assertEqual(vf["main"]["component"], "LFP cell")
        self.assertEqual(vf["primary_currency"], "USD")
        self.assertEqual(set(vf["currencies"]), {"USD", "INR"})                             # never mixed
        usd = vf["currencies"]["USD"]
        self.assertEqual((usd["items"], usd["with_data"]), (3, 2))
        # cell template: lithium 32% x -20% plus copper 6% x +1% = -5.84% -> justified 93.66
        cell = vf["main"]
        just = 100 * (1 - 0.32 * 0.2 + 0.06 * 0.01)
        self.assertAlmostEqual(cell["justified_price"], just)
        self.assertAlmostEqual(cell["extra"], (104 - just) * 50)
        self.assertEqual(cell["cheaper_peer"]["vendor"], "Beta")                            # Beta sells it 5.05% cheaper
        self.assertGreater(usd["weighted_gap"], 0.05)
        self.assertEqual([f["component"] for f in vf["concerns"]], ["LFP cell"])            # busbar is in line, widget has no data

    def test_points_cover_everything_in_one_call(self):
        vf = negotiation.vendor_facts(tree(), "123", "A", today=TODAY)
        text = " ".join(negotiation.vendor_points(vf))
        self.assertIn("all 4 products", text)
        self.assertIn("one conversation", text)
        self.assertIn("LFP cell", text)
        self.assertIn("Copper busbar 3x20", text)       # mentioned as fine
        self.assertIn("Mystery widget", text)           # mentioned as lacking data
        self.assertIn("My ask", text)
        self.assertIn("Beta", text)
        self.assertIn("not your contract costs", text)

    def test_works_when_only_one_product_has_market_data(self):
        wipe()
        seed([line("c1", "2026-06-01", "C", "Gamma", "e1", "Epoxy potting 1kg", 20.0, 10),
              line("c2", "2026-10-01", "C", "Gamma", "w1", "Mystery widget", 5.0, 100)])
        driver("bpa", [("2026-06-01", 100), ("2026-10-20", 90)], "123")
        vf = negotiation.vendor_facts(tree(), "123", "C", today=TODAY)
        pts = negotiation.vendor_points(vf)
        text = " ".join(pts)
        self.assertEqual(vf["currencies"]["USD"]["with_data"], 1)
        self.assertIn("Epoxy potting 1kg", text)
        self.assertIn("no market data", text)           # for the widget
        self.assertTrue(any(p.startswith("My ask") for p in pts))

    def test_biggest_area_without_data_is_called_out(self):
        wipe()
        seed([line("d1", "2026-06-01", "D", "Delta", "w1", "Mystery widget", 50.0, 1000),
              line("d2", "2026-06-01", "D", "Delta", "c1", "LFP cell", 10.0, 1)])
        driver("lith", [("2026-06-01", 100), ("2026-10-20", 90)], "123")
        vf = negotiation.vendor_facts(tree(), "123", "D", today=TODAY)
        self.assertEqual(vf["main"]["component"], "Mystery widget")
        self.assertIn("biggest area but we have no market data", " ".join(negotiation.vendor_points(vf)))

    def test_no_market_data_at_all_still_gives_a_usable_brief(self):
        wipe(); seed(alpha_rows())
        vf = negotiation.vendor_facts(tree(), "123", "A", today=TODAY)
        pts = negotiation.vendor_points(vf)
        self.assertTrue(any(p.startswith("My ask") for p in pts))
        self.assertIn("no market data", " ".join(pts))

    def test_user_template_override_wins_over_name_guess(self):
        with db.connect() as c:
            pid = c.execute("INSERT INTO products (mobile,name,created_at) VALUES ('123','Pack','x')").lastrowid
            c.execute("INSERT INTO components (product_id,mobile,name,qty,template,source,item_key) VALUES (?,?,?,?,?,?,?)",
                      (pid, "123", "widget", 1, "busbar", "manual", "p2|USD"))
        driver("nickel", [("2026-06-01", 100), ("2026-10-20", 100)], "123")
        vf = negotiation.vendor_facts(tree(), "123", "A", today=TODAY)
        widget = next(f for f in vf["items"] if f["component"] == "Mystery widget")
        self.assertEqual(widget["template_id"], "busbar")

    def test_small_gaps_are_not_raised_and_steady_rates_read_naturally(self):
        wipe()
        # copper +1.5% => busbar justified is 0.6 x 1.5% = 0.9% below... a 0.9% gap is under the 3% threshold
        seed([line("s1", "2026-06-01", "S", "Steady", "n1", "Nickel Strip Plated 1P 6x0.15 mm", 100.0, 10),
              line("s2", "2026-10-01", "S", "Steady", "n1", "Nickel Strip Plated 1P 6x0.15 mm", 100.0, 10)])
        driver("copper", [("2026-06-01", 100), ("2026-10-20", 98.5)])      # copper -1.5% -> justified 99.1, gap +0.9%
        vf = negotiation.vendor_facts(tree(), "123", "S", today=TODAY)
        self.assertEqual(vf["concerns"], [])                               # +0.9% is within the estimate's noise
        text = " ".join(negotiation.vendor_points(vf))
        self.assertIn("has been steady at", text)                          # not "from 100.00 to 100.00 (+0.0%)"
        self.assertNotIn("+0.0%", text)

    def test_nickel_strip_with_holder_is_a_strip_not_a_holder(self):
        from app import catalog
        self.assertEqual(catalog.classify("Nickel Strip Plated 2P(32650) 46.5x0.15 mm CD 34.5 with holder with slit"), "busbar")
        self.assertEqual(catalog.classify("Cell holder 18650 x5"), "holder")

    def test_raised_products_carry_past_trends_of_their_drivers(self):
        vf = negotiation.vendor_facts(tree(), "123", "A", today=TODAY)
        cell = vf["main"]                                              # raised: drivers imply a lower price
        lith = next(t for t in cell["trend"] if t["driver"] == "Lithium carbonate")
        self.assertEqual(lith["points"][0], ["2026-06-01", 100.0])      # every line starts at 100 on the first purchase
        self.assertAlmostEqual(lith["points"][-1][1], 80.0)             # lithium fell 20% since then
        self.assertAlmostEqual(lith["change"], -0.2)
        self.assertEqual(cell["price_index"][0], ["2026-06-01", 100.0])
        self.assertEqual(cell["price_index"][-1], [TODAY, 104.0])       # your price: 100 -> 104, extended to today
        ok = next(f for f in vf["items"] if f["component"] == "Copper busbar 3x20")
        self.assertNotIn("trend", ok)                                    # trends only for the products to raise

    def test_trend_chart_draws_lines_and_handles_no_data(self):
        from app import charts
        self.assertEqual(str(charts.trend_chart([], [])), "")
        svg = str(charts.trend_chart([{"driver": "Copper <b>", "points": [["2026-06-01", 100.0], ["2026-10-20", 90.0]], "change": -0.1}],
                                     [["2026-06-01", 100.0], ["2026-10-20", 100.0]]))
        self.assertIn("<polyline", svg); self.assertIn("2026-06-01", svg); self.assertIn("-10.0%", svg)
        self.assertNotIn("<b>", svg)                                      # driver names are escaped

    def test_unknown_vendor_and_ai_payload_is_numbers_only(self):
        self.assertIn("error", negotiation.vendor_facts(tree(), "123", "nope"))
        payload = negotiation.ai_payload(negotiation.vendor_facts(tree(), "123", "A", today=TODAY))
        self.assertEqual(payload["vendor"], "Alpha")
        self.assertIn("main_area", payload)
        self.assertLessEqual(len(payload["products"]), 12)


if __name__ == "__main__":
    unittest.main()
