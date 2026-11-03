"""Tests for products/components (E8, E9), cost drivers (E3-lite) and talking points (E7). No network."""
import io
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

from app import ai, analysis, catalog, config, create_app, db, drivers, importers, matching, negotiation  # noqa: E402


def wipe():
    # Safety: never wipe the real application database.
    assert config.DB_PATH.parent != config.ROOT / "data", "tests must use a temporary NEGOTIATOR_DATA_DIR"
    db.init()
    with db.connect() as c:
        for t in ("components", "products", "bill_lines", "driver_points", "connections"):
            c.execute(f"DELETE FROM {t}")


def seed_lines(mobile="123"):
    """Two vendors sell the LFP cell; one buys holders. Rates rose between Jun and Oct."""
    rows = []
    def add(bill, d, vid, vname, item, name, rate, qty=100, cur="USD"):
        rows.append((mobile, bill, "BN-" + bill, d, "paid", vid, vname, cur, item, name, "", "Raw Materials", "goods",
                     "pcs", rate, qty))
    add("b1", "2026-06-01", "v1", "PowerCell", "i1", "LFP prismatic cell 280Ah", 30.0)
    add("b2", "2026-10-01", "v1", "PowerCell", "i1", "LFP prismatic cell 280Ah", 36.0)
    add("b3", "2026-10-05", "v2", "EnerCell", "i1", "LFP prismatic cell 280Ah", 33.0)
    add("b4", "2026-09-01", "v3", "PolyMould", "i3", "Cell holder 18650 x5", 12.0, cur="INR")
    with db.connect() as c:
        c.executemany("INSERT INTO bill_lines (mobile,bill_id,bill_number,bill_date,status,vendor_id,vendor_name,currency,"
                      "item_id,item_name,sku,account_name,product_type,unit,rate,quantity) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)


def seed_driver(driver, points, mobile=""):
    with db.connect() as c:
        c.executemany("INSERT OR REPLACE INTO driver_points (driver,mobile,date,value) VALUES (?,?,?,?)",
                      [(driver, mobile, d, v) for d, v in points])


class CatalogMatchTest(unittest.TestCase):
    def test_classifier_prefers_specific_templates(self):
        cases = {"Cell holder 18650 x5": "holder", "LFP prismatic cell 280Ah": "cell", "Smart BMS CAN 16S": "bms",
                 "Epoxy potting 1kg": "epoxy", "Nickel strip 0.15mm": "busbar", "Silicone wire 14AWG": "wire",
                 "Battery box 48V": "box", "Foam insert": "pkg", "Electrolyte LFP 20L": "sep", "Mystery widget": "other"}
        for name, want in cases.items():
            self.assertEqual(catalog.classify(name), want, name)

    def test_matching_finds_the_right_item_and_rejects_weak_ones(self):
        prods = {"a": {"name": "LFP prismatic cell 280Ah"}, "b": {"name": "Cell holder 18650 x5"},
                 "c": {"name": "Copper busbar 3x20"}}
        self.assertEqual(matching.best_match("LFP cell 280Ah", prods)[0], "a")
        self.assertEqual(matching.best_match("cell holder 18650", prods)[0], "b")
        self.assertEqual(matching.best_match("Totally unrelated thing", prods), (None, 0.0))

    def test_matching_handles_spelling_variants(self):
        prods = {"a": {"name": "COPPER BUS BAR Universal (55x20x2mm) - Tin Coated"},
                 "b": {"name": "LITHIUM-ION CELL 2600 mAh DMEGC 18650"}}
        self.assertEqual(matching.best_match("Copper busbar 55x20x2mm", prods)[0], "a")
        self.assertEqual(matching.best_match("Li-ion cell 2600mAh 18650", prods)[0], "b")


class ImportersTest(unittest.TestCase):
    def test_csv_with_header_and_quantities(self):
        data = b"Component,Qty,Unit\nLFP cell 280Ah,16,pcs\nSmart BMS 16S,1,pcs\n,,\n"
        comps = importers.parse_upload("bom.csv", data)
        self.assertEqual([(c["name"], c["qty"], c["template"]) for c in comps],
                         [("LFP cell 280Ah", 16.0, "cell"), ("Smart BMS 16S", 1.0, "bms")])

    def test_csv_without_header(self):
        comps = importers.parse_upload("x.csv", b"Battery box 48V,2\nFoam insert,4\n")
        self.assertEqual([(c["name"], c["qty"]) for c in comps], [("Battery box 48V", 2.0), ("Foam insert", 4.0)])

    def test_xlsx(self):
        from openpyxl import Workbook
        wb = Workbook(); ws = wb.active
        ws.append(["Part description", "Quantity"]); ws.append(["Nickel strip 0.15mm", 40]); ws.append(["Epoxy potting 1kg", 2])
        buf = io.BytesIO(); wb.save(buf)
        comps = importers.parse_upload("bom.xlsx", buf.getvalue())
        self.assertEqual([(c["name"], c["qty"], c["template"]) for c in comps],
                         [("Nickel strip 0.15mm", 40.0, "busbar"), ("Epoxy potting 1kg", 2.0, "epoxy")])

    def test_rejections(self):
        with self.assertRaises(importers.ImportProblem):
            importers.parse_upload("x.docx", b"abc")
        with self.assertRaises(importers.ImportProblem):
            importers.parse_upload("x.csv", b"a" * (importers.MAX_BYTES + 1))
        with self.assertRaises(importers.ImportProblem):
            importers.parse_upload("x.csv", b"\n\n")
        with mock.patch.object(config, "ANTHROPIC_API_KEY", ""):
            with self.assertRaisesRegex(importers.ImportProblem, "needs AI"):
                importers.parse_upload("x.pdf", b"%PDF-1.4")


class AITest(unittest.TestCase):
    def test_clean_components_is_defensive(self):
        raw = [{"name": " LFP cell ", "qty": "16", "unit": "pcs", "category": "cell"},
               {"name": "Thing", "qty": -5, "category": "<script>"}, {"name": ""}, "junk", {"qty": 1}]
        out = ai.clean_components(raw)
        self.assertEqual([(c["name"], c["qty"], c["template"]) for c in out], [("LFP cell", 16.0, "cell"), ("Thing", 1.0, "other")])

    def test_generate_components_parses_json_reply(self):
        reply = 'Sure!\n[{"name": "Smart BMS 16S", "qty": 1, "unit": "pcs", "category": "bms"}]'
        with mock.patch.object(config, "ANTHROPIC_API_KEY", "k"), mock.patch.object(ai, "_complete", return_value=reply) as m:
            out = ai.generate_components("48V pack", "")
        self.assertEqual(out[0]["template"], "bms")
        self.assertIn("untrusted", m.call_args[0][0])  # the system prompt tells the model to treat <data> as data

    def test_not_configured_raises(self):
        with mock.patch.object(config, "ANTHROPIC_API_KEY", ""):
            with self.assertRaises(ai.AIError):
                ai.generate_components("x")


class DriversTest(unittest.TestCase):
    def setUp(self):
        wipe()

    def test_csv_points_and_changes(self):
        pts = drivers.parse_csv_points(b"date,value\n2026-06-01,100\n2026-10-01,120\nbad,row\n2026-10-02,-5\n")
        self.assertEqual(pts, [("2026-06-01", 100.0), ("2026-10-01", 120.0)])
        self.assertAlmostEqual(drivers.change(pts, "2026-06-01", "2026-10-01"), 0.2)
        self.assertAlmostEqual(drivers.change(pts, "2026-07-15", "2026-12-31"), 0.2)  # carries last value forward
        self.assertIsNone(drivers.change([], "2026-06-01", "2026-10-01"))

    def test_manual_upload_rules(self):
        self.assertEqual(drivers.add_manual("123", "lith", b"2026-06-01,10\n2026-10-01,12\n"), 2)
        self.assertEqual(len(drivers.series("lith", "123")), 2)
        self.assertEqual(drivers.series("lith", "999"), [])            # another user's benchmark is not visible
        with self.assertRaises(ValueError):
            drivers.add_manual("123", "copper", b"2026-06-01,1")       # Yahoo-backed drivers are not uploadable
        with self.assertRaises(ValueError):
            drivers.add_manual("123", "lith", b"nothing valid")

    def test_yahoo_refresh_stores_points_and_reports_errors(self):
        good = mock.Mock(); good.json.return_value = {"chart": {"result": [{"timestamp": [1780000000, 1780086400],
                         "indicators": {"quote": [{"close": [4.0, None]}]}}]}}
        bad = mock.Mock(); bad.json.side_effect = ValueError("boom")
        def fake_get(url, **kw):
            return bad if "BZ=F" in url else good
        with mock.patch("app.drivers.requests.get", side_effect=fake_get):
            res = drivers.refresh_yahoo()
        self.assertIn("copper", res["ok"]); self.assertIn("crude", res["errors"])
        self.assertEqual(len(drivers.series("copper", "123")), 1)  # the None close was dropped


class NegotiationTest(unittest.TestCase):
    def setUp(self):
        wipe(); seed_lines()
        with db.connect() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM bill_lines WHERE mobile='123'")]
        self.tree = analysis.build_tree(rows)
        self.comp = {"name": "LFP cell", "template": "cell", "item_key": "i1|USD"}

    def test_errors_when_unmatched_or_no_template(self):
        self.assertIn("error", negotiation.build_facts({**self.comp, "item_key": None}, self.tree, "123"))
        self.assertIn("error", negotiation.build_facts({**self.comp, "template": "other"}, self.tree, "123"))

    def test_facts_and_points_with_driver_data(self):
        # lithium +20% since June, +10% by the increase; aluminum -10% since June
        seed_driver("lith", [("2026-06-01", 100), ("2026-10-01", 110), ("2026-10-20", 120)], "123")
        seed_driver("alu", [("2026-06-01", 100), ("2026-10-20", 90)])
        f = negotiation.build_facts(self.comp, self.tree, "123", today="2026-10-20")
        self.assertEqual(f["vendor_id"], "v2")  # most recent purchase (Oct 5) -> but check explicit vendor too
        f = negotiation.build_facts(self.comp, self.tree, "123", vendor_id="v1", today="2026-10-20")
        self.assertEqual((f["first_rate"], f["latest_rate"]), (30.0, 36.0))
        self.assertAlmostEqual(f["vendor_change"], 0.2)
        # implied = 0.32*0.20 + 0.04*(-0.10) = 0.06  -> justified 30 * 1.06
        self.assertAlmostEqual(f["justified_price"], 30 * 1.06)
        self.assertAlmostEqual(f["gap"], 36 / (30 * 1.06) - 1)
        self.assertAlmostEqual(f["supported_at_increase"], 0.32 * 0.10)
        self.assertAlmostEqual(f["coverage"], (0.32 + 0.04) / 0.61)
        self.assertEqual([p["vendor"] for p in f["peers"]], ["EnerCell"])
        pts = negotiation.deterministic_points(f)
        text = " ".join(pts)
        self.assertIn("larger than the cost drivers supported", text)
        self.assertIn("Ask: revise the rate", text)
        self.assertIn("partial", text)
        self.assertIn("not the vendor's contract costs", text)

    def test_no_driver_data_gives_a_helpful_message(self):
        f = negotiation.build_facts(self.comp, self.tree, "123", vendor_id="v1", today="2026-10-20")
        self.assertEqual(f["coverage"], 0.0)
        self.assertIn("no market data", negotiation.deterministic_points(f)[0])

    def test_ai_is_used_when_configured_and_falls_back_on_error(self):
        seed_driver("lith", [("2026-06-01", 100), ("2026-10-20", 120)], "123")
        f = negotiation.build_facts(self.comp, self.tree, "123", vendor_id="v1", today="2026-10-20")
        with mock.patch.object(config, "ANTHROPIC_API_KEY", "k"), mock.patch.object(ai, "talking_points", return_value=["x"]):
            self.assertEqual(negotiation.talking_points(f), (["x"], "ai"))
        with mock.patch.object(config, "ANTHROPIC_API_KEY", "k"), mock.patch.object(ai, "talking_points", side_effect=ai.AIError("no")):
            self.assertEqual(negotiation.talking_points(f)[1], "rules")
        with mock.patch.object(config, "ANTHROPIC_API_KEY", ""):
            self.assertEqual(negotiation.talking_points(f)[1], "rules")


class RoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(); cls.app.testing = True

    def setUp(self):
        wipe(); seed_lines()
        self.c = self.app.test_client()
        html = self.c.get("/login").get_data(as_text=True)
        self.t = html.split('name="csrf" value="')[1].split('"')[0]
        self.c.post("/login", data={"mobile": "123", "csrf": self.t})

    def post(self, url, **data):
        return self.c.post(url, data={"csrf": self.t, **data}, follow_redirects=True)

    def make_product(self):
        r = self.c.post("/products", data={"csrf": self.t, "name": "48V pack", "description": "LFP"})
        return int(r.headers["Location"].rsplit("/", 1)[1])

    def test_pages_require_login(self):
        anon = self.app.test_client()
        for url in ("/products", "/drivers", "/products/1"):
            self.assertEqual(anon.get(url).status_code, 302)

    def test_create_add_upload_update_delete(self):
        pid = self.make_product()
        self.post(f"/products/{pid}/components", name="LFP cell 280Ah", qty="16", unit="pcs")
        up = {"csrf": self.t, "file": (io.BytesIO(b"Component,Qty\nCell holder 18650 x5,8\nMystery widget,1\n"), "bom.csv")}
        html = self.c.post(f"/products/{pid}/upload", data=up, content_type="multipart/form-data",
                           follow_redirects=True).get_data(as_text=True)
        self.assertIn("Added 2 components", html)
        with db.connect() as c:
            comps = {r["name"]: dict(r) for r in c.execute("SELECT * FROM components WHERE product_id=?", (pid,))}
        self.assertEqual(comps["LFP cell 280Ah"]["item_key"], "i1|USD")        # auto-matched to Zoho
        self.assertEqual(comps["Cell holder 18650 x5"]["template"], "holder")
        self.assertIsNone(comps["Mystery widget"]["item_key"])
        cid = comps["Mystery widget"]["id"]
        self.post(f"/products/{pid}/components/{cid}", template="box", item_key="i3|INR")
        with db.connect() as c:
            row = c.execute("SELECT template,item_key FROM components WHERE id=?", (cid,)).fetchone()
        self.assertEqual((row["template"], row["item_key"]), ("box", "i3|INR"))
        self.assertEqual(self.c.post(f"/products/{pid}/components/{cid}", data={"csrf": self.t, "template": "nope"}).status_code, 400)
        self.assertEqual(self.c.post(f"/products/{pid}/components/{cid}", data={"csrf": self.t, "template": "box", "item_key": "zzz"}).status_code, 400)
        self.post(f"/products/{pid}/components/{cid}/delete")
        self.post(f"/products/{pid}/delete")
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM products").fetchone()[0], 0)

    def test_other_users_data_is_not_reachable(self):
        with db.connect() as c:
            cur = c.execute("INSERT INTO products (mobile,name,created_at) VALUES ('999','Secret','x')")
        self.assertEqual(self.c.get(f"/products/{cur.lastrowid}").status_code, 404)
        self.assertEqual(self.c.post(f"/products/{cur.lastrowid}/delete", data={"csrf": self.t}).status_code, 404)

    def test_generate_without_ai_shows_not_configured(self):
        pid = self.make_product()
        with mock.patch.object(config, "ANTHROPIC_API_KEY", ""):
            html = self.post(f"/products/{pid}/generate").get_data(as_text=True)
        self.assertIn("AI is not configured", html)

    def test_generate_with_ai_adds_components(self):
        pid = self.make_product()
        reply = [{"name": "Smart BMS 16S", "qty": 1, "unit": "pcs", "category": "bms"}]
        with mock.patch.object(config, "ANTHROPIC_API_KEY", "k"), mock.patch.object(ai, "generate_components", return_value=ai.clean_components(reply)):
            html = self.post(f"/products/{pid}/generate").get_data(as_text=True)
        self.assertIn("AI suggested 1 components", html)

    def test_pdf_upload_without_ai_is_explained(self):
        pid = self.make_product()
        with mock.patch.object(config, "ANTHROPIC_API_KEY", ""):
            html = self.c.post(f"/products/{pid}/upload", data={"csrf": self.t, "file": (io.BytesIO(b"%PDF-1.4"), "bom.pdf")},
                               content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
        self.assertIn("needs AI", html)

    def test_talking_points_page(self):
        pid = self.make_product()
        self.post(f"/products/{pid}/components", name="LFP cell 280Ah", qty="16")
        with db.connect() as c:
            cid = c.execute("SELECT id FROM components").fetchone()["id"]
        url = f"/products/{pid}/components/{cid}/talking-points"
        self.assertIn("no market data", self.c.get(url).get_data(as_text=True))  # matched, but no drivers yet
        seed_driver("lith", [("2026-06-01", 100), ("2026-12-31", 130)], "123")
        with mock.patch.object(config, "ANTHROPIC_API_KEY", ""):
            html = self.c.get(url + "?vendor=v1").get_data(as_text=True)
        self.assertIn("template-written", html)
        self.assertIn("Lithium carbonate", html)
        # unmatched component explains what to do
        self.post(f"/products/{pid}/components/{cid}", template="cell", item_key="")
        self.assertIn("not matched", self.c.get(url).get_data(as_text=True))

    def test_drivers_page_and_upload(self):
        self.assertIn("Lithium carbonate", self.c.get("/drivers").get_data(as_text=True))
        up = {"csrf": self.t, "file": (io.BytesIO(b"2026-06-01,10\n2026-10-01,12\n"), "li.csv")}
        html = self.c.post("/drivers/lith/upload", data=up, content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
        self.assertIn("Saved 2 benchmark points", html)
        up = {"csrf": self.t, "file": (io.BytesIO(b"2026-06-01,10\n"), "c.csv")}
        html = self.c.post("/drivers/copper/upload", data=up, content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
        self.assertIn("does not take uploaded benchmarks", html)


if __name__ == "__main__":
    unittest.main()
