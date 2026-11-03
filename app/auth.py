from functools import wraps

from flask import redirect, session, url_for


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if not session.get("mobile"):
            return redirect(url_for("login"))
        return view(*a, **kw)
    return wrapper
