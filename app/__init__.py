import re
import secrets
import time
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for

from . import analysis, config, db, zoho

API_DOMAIN_RE = re.compile(r"^https://([a-z0-9-]+\.)*zohoapis\.[a-z.]+$")


def create_app():
    app = Flask(__name__)
    app.secret_key = config.SECRET_KEY
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    db.init()

    # ------------------------------------------------------------ helpers
    @app.before_request
    def csrf_check():
        if request.method == "POST":
            sent, expected = request.form.get("csrf", ""), session.get("csrf", "")
            if not expected or not secrets.compare_digest(sent, expected):
                abort(400, "Invalid or missing form token. Reload the page and try again.")

    @app.context_processor
    def inject():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        return {"csrf_token": session["csrf"], "mobile": session.get("mobile")}

    @app.template_filter("money")
    def money(value):
        return f"{value:,.2f}"

    def login_required(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if not session.get("mobile"):
                return redirect(url_for("login"))
            return view(*a, **kw)
        return wrapper

    def connection():
        with db.connect() as conn:
            row = conn.execute(
                "SELECT mobile, dc, org_id, org_name, connected_at, last_sync_at, last_sync_status "
                "FROM connections WHERE mobile=?", (session["mobile"],)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------ auth
    @app.get("/")
    def home():
        return redirect(url_for("connect" if session.get("mobile") else "login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            number = request.form.get("mobile", "").strip()
            if config.AUTH_DEMO and number == config.DEMO_MOBILE:
                csrf = session.get("csrf")
                session.clear()
                session.update(mobile=number, csrf=csrf or secrets.token_urlsafe(24))
                return redirect(url_for("connect"))
            flash("This mobile number isn't recognised. In this demo version only the demo number works.", "error")
        return render_template("login.html", demo=config.AUTH_DEMO, demo_mobile=config.DEMO_MOBILE)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------ Zoho connector
    @app.get("/connect")
    @login_required
    def connect():
        return render_template(
            "connect.html", conn=connection(), scopes=zoho.SCOPES, dcs=zoho.DC_LABELS, default_dc=config.ZOHO_DC,
            configured=bool(config.ZOHO_CLIENT_ID and config.ZOHO_CLIENT_SECRET), redirect_uri=config.ZOHO_REDIRECT_URI)

    @app.post("/oauth/start")
    @login_required
    def oauth_start():
        if not (config.ZOHO_CLIENT_ID and config.ZOHO_CLIENT_SECRET):
            flash("Zoho client credentials are not configured on the server yet. See the setup steps below.", "error")
            return redirect(url_for("connect"))
        dc = request.form.get("dc", config.ZOHO_DC)
        if dc not in zoho.ACCOUNTS:
            abort(400, "Unknown Zoho data center")
        state = secrets.token_urlsafe(24)
        session["oauth"] = {"state": state, "dc": dc}
        return redirect(zoho.authorize_url(dc, state))

    @app.get("/oauth/callback")
    @login_required
    def oauth_callback():
        pending = session.pop("oauth", None)
        if request.args.get("error"):
            flash(f"Zoho sign-in was not completed ({request.args['error']}).", "error")
            return redirect(url_for("connect"))
        if not pending or not secrets.compare_digest(request.args.get("state", ""), pending["state"]):
            flash("That sign-in link has expired or is invalid. Please start again.", "error")
            return redirect(url_for("connect"))
        code = request.args.get("code")
        if not code:
            flash("Zoho did not return an authorization code.", "error")
            return redirect(url_for("connect"))
        accounts_url = zoho.trusted_accounts_url(request.args.get("accounts-server"), pending["dc"])
        try:
            tok = zoho.exchange_code(accounts_url, code)
            api_domain = tok.get("api_domain", "")
            if not API_DOMAIN_RE.match(api_domain):
                raise zoho.ZohoError("Zoho returned an unexpected API address")
            if not tok.get("refresh_token"):
                raise zoho.ZohoError("Zoho did not return a refresh token. Remove the app under Zoho "
                                     "Connected Apps and connect again.")
            org_id, org_name = zoho.fetch_organization(api_domain, tok["access_token"])
        except zoho.ZohoError as exc:
            flash(str(exc), "error")
            return redirect(url_for("connect"))
        from .crypto import encrypt
        with db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO connections (mobile,dc,accounts_url,api_domain,org_id,org_name,access_token,"
                "refresh_token,expires_at,connected_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (session["mobile"], pending["dc"], accounts_url, api_domain, org_id, org_name,
                 encrypt(tok["access_token"]), encrypt(tok["refresh_token"]),
                 time.time() + int(tok.get("expires_in", 3600)) - 60,
                 datetime.now(timezone.utc).isoformat(timespec="seconds")))
        zoho.start_sync(session["mobile"])
        flash(f"Connected to Zoho Books: {org_name or org_id}. Pulling your bills now.", "ok")
        return redirect(url_for("dashboard"))

    @app.post("/sync")
    @login_required
    def sync():
        if not connection():
            return redirect(url_for("connect"))
        zoho.start_sync(session["mobile"])
        return redirect(url_for("dashboard"))

    @app.get("/sync/status")
    @login_required
    def sync_status():
        return jsonify(zoho.PROGRESS.get(session["mobile"], {"state": "idle"}))

    @app.post("/disconnect")
    @login_required
    def disconnect():
        mobile = session["mobile"]
        with db.connect() as conn:
            row = conn.execute("SELECT accounts_url, refresh_token FROM connections WHERE mobile=?", (mobile,)).fetchone()
            if row:
                from .crypto import decrypt
                zoho.revoke(row["accounts_url"], decrypt(row["refresh_token"]))
            conn.execute("DELETE FROM connections WHERE mobile=?", (mobile,))
            conn.execute("DELETE FROM bill_lines WHERE mobile=?", (mobile,))
        zoho.PROGRESS.pop(mobile, None)
        flash("Disconnected from Zoho. Your stored Zoho data has been deleted.", "ok")
        return redirect(url_for("connect"))

    # ------------------------------------------------------------ data
    @app.get("/dashboard")
    @login_required
    def dashboard():
        conn_row = connection()
        if not conn_row:
            return redirect(url_for("connect"))
        with db.connect() as conn:
            rows = [dict(r) for r in conn.execute("SELECT * FROM bill_lines WHERE mobile=?", (session["mobile"],))]
        tree = analysis.build_tree(rows)
        stats = {
            "bills": len({r["bill_id"] for r in rows}), "lines": len(rows),
            "vendors": len(tree["vendors"]), "products": len(tree["products"]),
            "first": min((r["bill_date"] for r in rows), default=None),
            "last": max((r["bill_date"] for r in rows), default=None),
        }
        return render_template("dashboard.html", conn=conn_row, tree=tree, stats=stats,
                               running=zoho.sync_running(session["mobile"]))

    return app
