"""Talking points tab: briefs are generated only on request, saved, and shown later without regenerating. No network."""
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

from app import ai, config, create_app, db, saved  # noqa: E402
from test_vendor_brief import alpha_drivers, alpha_rows, driver, line, seed, wipe  # noqa: E402


class TalkingTabTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app(); cls.app.testing = True

    def setUp(self):
        wipe(); seed(alpha_rows()); alpha_drivers()
        self.c = self.app.test_client()
        t = self.c.get("/login").get_data(as_text=True).split('name="csrf" value="')[1].split('"')[0]
        self.t = t
        self.c.post("/login", data={"mobile": "123", "csrf": t})

    def gen(self, vendor="A", **patches):
        """Click Generate/Regenerate for a vendor; AI is off unless a key is patched in."""
        key = patches.pop("key", "")
        with mock.patch.object(config, "ANTHROPIC_API_KEY", key):
            return self.c.post("/talking-points/vendor", data={"csrf": self.t, "vendor": vendor}, follow_redirects=True).get_data(as_text=True)

    def page(self, url):
        return self.c.get(url).get_data(as_text=True)

    # ------------------------------------------------------------ the tab
    def test_login_required_and_nav_tab(self):
        anon = self.app.test_client()
        for u in ("/talking-points", "/talking-points/vendor?vendor=A", "/vendors"):
            self.assertEqual(anon.get(u).status_code, 302)
        self.assertIn(">Talking points</a>", self.page("/alerts"))        # a tab in the top bar

    def test_hub_lists_every_vendor_as_not_generated_and_has_no_side_effects(self):
        with mock.patch.object(ai, "vendor_talking_points", side_effect=AssertionError("must not generate on view")):
            html = self.page("/talking-points")
        self.assertIn("Alpha", html); self.assertIn("Beta", html)
        self.assertEqual(html.count("Not generated yet"), 2)
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM talking_points").fetchone()[0], 0)   # nothing saved by viewing

    def test_viewing_a_vendor_before_generating_shows_an_empty_state_not_a_brief(self):
        html = self.page("/talking-points/vendor?vendor=A")
        self.assertIn("No talking points generated yet for Alpha", html)
        self.assertIn("Generate talking points", html)
        self.assertNotIn("Talking points for the call", html)

    # ------------------------------------------------------------ generate once, view many times
    def test_generate_saves_and_later_views_do_not_regenerate(self):
        html = self.gen("A")
        self.assertIn("Call brief: Alpha", html); self.assertIn("all 4 products", html)
        self.assertIn("template-written", html); self.assertIn("AI wording was not used", html)
        for name in ("LFP cell", "Mystery widget", "Copper busbar 3x20", "Cell holder"):
            self.assertIn(name, html)
        self.assertIn('class="pill bad"', html)
        self.assertIn("Opening this page does not regenerate it", html)
        # open the page and the hub repeatedly: nothing is regenerated, nothing new is saved
        with mock.patch.object(ai, "vendor_talking_points", side_effect=AssertionError("regenerated!")), \
             mock.patch("app.negotiation.vendor_facts", side_effect=AssertionError("recomputed facts!")), \
             mock.patch.object(config, "ANTHROPIC_API_KEY", "k"):
            for _ in range(3):
                again = self.page("/talking-points/vendor?vendor=A")
                self.assertIn("all 4 products", again)
                self.assertIn('class="trendrow"', again)                      # trends come from the saved snapshot too
                self.assertIn("template-written", again)
            self.assertNotIn("Not generated yet", self.page("/talking-points").split("Alpha")[1].split("Beta")[0])
        with db.connect() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM talking_points").fetchone()[0], 1)

    def test_products_table_lists_the_raise_ones_first_then_the_others(self):
        wipe()
        seed([line("r1", "2026-06-01", "R", "Rho", "big", "Copper busbar big", 10.0, 1000),     # big spend, in line -> OK
              line("r2", "2026-06-01", "R", "Rho", "mid", "Nickel strip mid", 10.0, 500),       # mid spend, in line -> OK
              line("r3", "2026-06-01", "R", "Rho", "sm1", "LFP cell small", 100.0, 1),           # tiny spend but drivers fell -> raise
              line("r4", "2026-10-01", "R", "Rho", "sm1", "LFP cell small", 104.0, 1),
              line("r5", "2026-06-01", "R", "Rho", "sm2", "LFP cell tiny", 50.0, 1),             # tinier spend, bigger overpayment? no, smaller
              line("r6", "2026-10-01", "R", "Rho", "sm2", "LFP cell tiny", 50.0, 1)])
        driver("lith", [("2026-06-01", 100), ("2026-10-20", 80)], "123")
        driver("copper", [("2026-06-01", 100), ("2026-10-20", 100)])
        driver("nickel", [("2026-06-01", 100), ("2026-10-20", 100)], "123")
        html = self.gen("R")
        pos = lambda name: html.index(f"<b>{name}</b>")
        self.assertLess(pos("LFP cell small"), pos("Copper busbar big"))      # raised first even though its spend is tiny
        self.assertLess(pos("LFP cell small"), pos("Nickel strip mid"))
        self.assertLess(pos("LFP cell tiny"), pos("Copper busbar big"))       # both cells are raised (drivers fell 6.4%)
        self.assertLess(pos("Copper busbar big"), pos("Nickel strip mid"))     # others stay in spend order, biggest first
        self.assertIn("To raise on the call (2)", html)
        self.assertIn("Others (2)", html)
        self.assertLess(html.index("To raise on the call"), html.index("Others (2)"))

    def test_raised_rows_show_a_trend_and_every_price_explains_itself_on_hover(self):
        html = self.gen("A")
        self.assertEqual(html.count('class="trendrow"'), 1)               # only LFP cell is raised
        self.assertIn('<svg class="trend"', html)
        self.assertIn("Since 2026-06-01 (all lines start at 100)", html)
        self.assertIn("Lithium carbonate", html)
        # the hover explanation for the LFP cell's "drivers imply" price
        self.assertIn("Why USD 93.66?", html)
        self.assertIn("Starts from USD 100.00, your first purchase on 2026-06-01", html)
        self.assertIn("Lithium carbonate: -20.0% since then × 32% of the price = <b>-6.40%</b>", html)
        self.assertIn("Total -6.34%", html)
        self.assertIn("assumed unchanged", html)
        self.assertIn("Your latest rate USD 104.00 is <b>+11.0%</b> above this", html)
        # products with no market data have no explanation to give
        self.assertEqual(html.count('class="tip"'), 2)                    # LFP cell and Copper busbar have data; the others don't

    def test_saved_brief_survives_a_new_app_session(self):
        self.gen("A")
        other = self.app.test_client()
        t = other.get("/login").get_data(as_text=True).split('name="csrf" value="')[1].split('"')[0]
        other.post("/login", data={"mobile": "123", "csrf": t})
        self.assertIn("all 4 products", other.get("/talking-points/vendor?vendor=A").get_data(as_text=True))

    def test_ai_version_is_saved_and_a_failed_regenerate_reports_the_reason(self):
        with mock.patch.object(ai, "vendor_talking_points", return_value=["AI point one"]):
            html = self.gen("A", key="k")
        self.assertIn("AI-written", html); self.assertIn("AI point one", html)
        with mock.patch.object(ai, "vendor_talking_points", side_effect=ai.AIError("credit too low")):
            html = self.gen("A", key="k")
        self.assertIn("credit too low", html); self.assertIn("template-written", html)

    # ------------------------------------------------------------ history, staleness, ownership
    def test_history_keeps_older_versions_and_latest_is_shown_by_default(self):
        with mock.patch.object(ai, "vendor_talking_points", return_value=["FIRST version"]):
            self.gen("A", key="k")
        with mock.patch.object(ai, "vendor_talking_points", return_value=["SECOND version"]):
            self.gen("A", key="k")
        latest = self.page("/talking-points/vendor?vendor=A")
        self.assertIn("SECOND version", latest); self.assertNotIn("FIRST version", latest)
        first_id = saved.history("123", "vendor", "A")[-1]["id"]
        old = self.page(f"/talking-points/vendor?vendor=A&id={first_id}")
        self.assertIn("FIRST version", old); self.assertIn("older version", old)

    def test_a_brief_goes_out_of_date_when_the_data_changes(self):
        self.gen("A")
        self.assertNotIn("have changed since this was generated", self.page("/talking-points/vendor?vendor=A"))
        seed([line("zz", "2026-10-10", "A", "Alpha", "p9", "New thing", 1.0, 1)])      # e.g. a new Zoho sync
        self.assertIn("have changed since this was generated", self.page("/talking-points/vendor?vendor=A"))
        self.assertIn("out of date", self.page("/talking-points"))
        driver("lith", [("2026-10-25", 70)], "123")                                      # or new market data
        self.assertIn("have changed since this was generated", self.page("/talking-points/vendor?vendor=A"))
        self.gen("A")                                                                    # regenerate clears the warning
        self.assertNotIn("have changed since this was generated", self.page("/talking-points/vendor?vendor=A"))

    def test_other_users_saved_briefs_are_not_reachable(self):
        sid = saved.save("999", "vendor", "A", "Alpha", ["secret point"], "rules", None, {"vendor": "Alpha"})
        self.assertEqual(self.c.get(f"/talking-points/vendor?vendor=A&id={sid}").status_code, 404)
        self.assertNotIn("secret point", self.page("/talking-points"))
        self.assertIsNone(saved.latest("123", "vendor", "A"))

    def test_unknown_vendor_and_legacy_urls(self):
        self.assertIn("not found", self.page("/talking-points/vendor?vendor=zzz"))
        self.assertIn("not found", self.c.post("/talking-points/vendor", data={"csrf": self.t, "vendor": "zzz"}, follow_redirects=True).get_data(as_text=True))
        self.assertEqual(self.c.post("/talking-points/vendor", data={"vendor": "A"}).status_code, 400)   # CSRF required
        r = self.c.get("/vendors"); self.assertEqual((r.status_code, r.headers["Location"]), (302, "/talking-points"))
        r = self.c.get("/vendors/talking-points?vendor=A"); self.assertTrue(r.headers["Location"].endswith("/talking-points/vendor?vendor=A"))

    def test_rates_page_links_to_the_saved_vendor_brief(self):
        from app.crypto import encrypt
        with db.connect() as c:
            c.execute("INSERT INTO connections VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", ("123", "in", "https://accounts.zoho.in",
                      "https://www.zohoapis.in", "1", "Org", encrypt("a"), encrypt("r"), 9e9, "x", None, None))
        self.assertIn("talking-points/vendor", self.page("/dashboard"))

    # ------------------------------------------------------------ component level uses the same save-once behaviour
    def test_component_talking_points_are_saved_too(self):
        with db.connect() as c:
            pid = c.execute("INSERT INTO products (mobile,name,created_at) VALUES ('123','Pack','x')").lastrowid
            cid = c.execute("INSERT INTO components (product_id,mobile,name,qty,template,source,item_key) VALUES (?,?,?,?,?,?,?)",
                            (pid, "123", "LFP cell", 1, "cell", "manual", "p1|USD")).lastrowid
        url = f"/products/{pid}/components/{cid}/talking-points"
        self.assertIn("No talking points generated yet", self.page(url))
        with mock.patch.object(config, "ANTHROPIC_API_KEY", ""):
            html = self.c.post(url, data={"csrf": self.t, "vendor": "A"}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("template-written", html); self.assertIn("Lithium carbonate", html)
        with mock.patch("app.negotiation.talking_points", side_effect=AssertionError("regenerated!")):
            self.assertIn("Lithium carbonate", self.page(url))                     # viewing does not regenerate
        self.assertIn("LFP cell", self.page("/talking-points"))                    # listed on the tab
        self.c.post(f"/products/{pid}/components/{cid}/delete", data={"csrf": self.t})
        self.assertIsNone(saved.latest("123", "component", cid))                   # removed with the component


if __name__ == "__main__":
    unittest.main()
