"""
MyGrocer – Flask backend
Single-file application: routes, DB, auth, CSRF, validation.

Environment variables (see .env.example):
    PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD
    FLASK_SECRET_KEY      (required, long random string)
    FLASK_COOKIE_SECURE   (optional, "1" in HTTPS production)
    FLASK_ENV             (optional, "production" disables debug)
"""

import os
import logging
from datetime import date, timedelta, datetime, timezone
from functools import wraps

import psycopg
from psycopg.rows import dict_row
from flask import (
    Flask, request, jsonify, session, render_template,
    redirect, url_for, abort,
)
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect, CSRFError

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("mygrocer")

app = Flask(__name__, template_folder="templates")

SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
IS_PROD = os.environ.get("FLASK_ENV", "development").lower() == "production"

if not SECRET_KEY:
    if IS_PROD:
        raise RuntimeError("FLASK_SECRET_KEY must be set in production")
    # Dev-only fallback so local testing isn't blocked; clearly insecure.
    SECRET_KEY = "dev-only-insecure-secret-change-me"
    log.warning("FLASK_SECRET_KEY not set – using insecure dev fallback")

app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("FLASK_COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),   # remember-me
    SESSION_REFRESH_EACH_REQUEST=False,
    WTF_CSRF_TIME_LIMIT=None,                        # token bound to session
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,              # 1 MB body cap
)

# CSRF: token stored in session; issued as XSRF-TOKEN cookie for JS to read.
csrf = CSRFProtect(app)
app.config["WTF_CSRF_CHECK_DEFAULT"] = False  # we check explicitly per-route


# --------------------------------------------------------------------------- #
# Database helpers
# --------------------------------------------------------------------------- #
def get_db():
    """Open a new psycopg connection using environment variables."""
    return psycopg.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "mygrocer_db"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD"),
        row_factory=dict_row,
        autocommit=False,
    )


def db_execute(sql, params=(), fetch=None):
    """
    Run a statement in a transaction.
    fetch: None | 'one' | 'all'  -> controls cursor method used.
    Returns the fetched rows (or rowcount for no-fetch).
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch == "one":
                row = cur.fetchone()
                conn.commit()
                return row
            if fetch == "all":
                rows = cur.fetchall()
                conn.commit()
                return rows
            rc = cur.rowcount
            conn.commit()
            return rc


# --------------------------------------------------------------------------- #
# Auth helpers
# --------------------------------------------------------------------------- #
def current_user_id():
    return session.get("user_id")


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user_id():
            if request.path.startswith("/api/"):
                return jsonify(error="authentication required"), 401
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


def json_body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None
    return data


def fail(msg, code=400, **extra):
    payload = {"error": msg}
    payload.update(extra)
    return jsonify(payload), code


# --------------------------------------------------------------------------- #
# CSRF: manual enforcement on cookie-authenticated state changers.
# The frontend reads the XSRF-TOKEN cookie (set by Flask-WTF) and sends it
# back as the X-CSRFToken header. We validate here.
# --------------------------------------------------------------------------- #
def csrf_protect():
    """Call at the start of every state-changing API route."""
    from flask_wtf.csrf import validate_csrf
    from wtforms.validators import ValidationError
    token = request.headers.get("X-CSRFToken") or request.form.get("csrf_token")
    try:
        validate_csrf(token)
    except (ValidationError, Exception) as e:
        log.info("CSRF failure: %s", e)
        abort(403, description="CSRF token missing or invalid")


@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    return jsonify(error="CSRF token missing or invalid"), 403


# --------------------------------------------------------------------------- #
# Session cookie: make sure a CSRF cookie is present on GET page loads.
# --------------------------------------------------------------------------- #
@app.after_request
def set_csrf_cookie(resp):
    # Flask-WTF injects csrf_token() into templates; we mirror it as a
    # readable cookie so fetch() can send it back in the header.
    if request.method == "GET" and resp.mimetype == "text/html":
        from flask_wtf.csrf import generate_csrf
        token = generate_csrf()
        resp.set_cookie(
            "XSRF-TOKEN", token,
            httponly=False,          # JS must read it
            samesite="Lax",
            secure=app.config["SESSION_COOKIE_SECURE"],
            max_age=60 * 60 * 8,
        )
    return resp


# --------------------------------------------------------------------------- #
# Page routes
# --------------------------------------------------------------------------- #
@app.route("/")
@app.route("/index.html")
def index():
    if current_user_id():
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register.html")
def register_page():
    if current_user_id():
        return redirect(url_for("dashboard"))
    return render_template("register.html")


@app.route("/dashboard.html")
@login_required
def dashboard():
    return render_template("dashboard.html")


@app.route("/kitchen.html")
@login_required
def kitchen():
    return render_template("kitchen.html")


@app.route("/shopping.html")
@login_required
def shopping():
    return render_template("shopping.html")


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    try:
        row = db_execute("SELECT current_database() AS db", fetch="one")
        return jsonify(status="ok", database=row["db"])
    except Exception:
        log.exception("Health check failed")
        return jsonify(status="error", error="database unavailable"), 500


# --------------------------------------------------------------------------- #
# Auth API
# --------------------------------------------------------------------------- #
@app.post("/api/register")
def api_register():
    csrf_protect()
    data = json_body()
    if not data:
        return fail("invalid JSON body")
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not name or len(name) > 100:
        return fail("name is required (max 100 chars)")
    if not email or "@" not in email or len(email) > 255:
        return fail("a valid email is required")
    if len(password) < 8:
        return fail("password must be at least 8 characters")

    pw_hash = generate_password_hash(password)
    try:
        row = db_execute(
            "INSERT INTO users (name, email, password_hash) "
            "VALUES (%s, %s, %s) RETURNING id, name, email",
            (name, email, pw_hash),
            fetch="one",
        )
        return jsonify(user=row), 201
    except psycopg.errors.UniqueViolation:
        return fail("an account with that email already exists", 409)
    except Exception:
        log.exception("register failed")
        return fail("server error", 500)


@app.post("/api/login")
def api_login():
    csrf_protect()
    data = json_body()
    if not data:
        return fail("invalid JSON body")
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    remember = bool(data.get("remember"))

    if not email or not password:
        return fail("email and password are required")

    try:
        row = db_execute(
            "SELECT id, name, email, password_hash FROM users WHERE email=%s",
            (email,),
            fetch="one",
        )
    except Exception:
        log.exception("login query failed")
        return fail("server error", 500)

    if not row or not check_password_hash(row["password_hash"], password):
        return fail("invalid email or password", 401)

    session.clear()
    session["user_id"] = row["id"]
    session["user_name"] = row["name"]
    session.permanent = remember
    return jsonify(user={"id": row["id"], "name": row["name"], "email": row["email"]})


@app.post("/api/logout")
def api_logout():
    csrf_protect()
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
@login_required
def api_me():
    row = db_execute(
        "SELECT id, name, email, created_at FROM users WHERE id=%s",
        (current_user_id(),),
        fetch="one",
    )
    if not row:
        session.clear()
        return fail("session invalid", 401)
    row["created_at"] = row["created_at"].isoformat()
    return jsonify(user=row)


# --------------------------------------------------------------------------- #
# Groceries API
# --------------------------------------------------------------------------- #
VALID_STATUS = {"active", "used", "expired"}


def _parse_date(s):
    if s in (None, ""):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ValueError("expiry_date must be YYYY-MM-DD")


def _parse_qty(q):
    try:
        q = int(q)
    except (TypeError, ValueError):
        raise ValueError("quantity must be an integer")
    if q < 1 or q > 999:
        raise ValueError("quantity must be between 1 and 999")
    return q


def _serialize_grocery(r):
    return {
        "id": r["id"],
        "item_name": r["item_name"],
        "quantity": r["quantity"],
        "expiry_date": r["expiry_date"].isoformat() if r["expiry_date"] else None,
        "status": r["status"],
        "created_at": r["created_at"].isoformat(),
    }


@app.get("/api/groceries")
@login_required
def api_groceries_list():
    rows = db_execute(
        "SELECT id, item_name, quantity, expiry_date, status, created_at "
        "FROM groceries WHERE user_id=%s ORDER BY created_at DESC",
        (current_user_id(),),
        fetch="all",
    )
    return jsonify(items=[_serialize_grocery(r) for r in rows])


@app.post("/api/groceries")
@login_required
def api_groceries_create():
    csrf_protect()
    data = json_body()
    if not data:
        return fail("invalid JSON body")
    name = (data.get("item_name") or "").strip()
    if not name or len(name) > 150:
        return fail("item_name is required (max 150 chars)")
    try:
        qty = _parse_qty(data.get("quantity", 1))
        exp = _parse_date(data.get("expiry_date"))
    except ValueError as e:
        return fail(str(e))

    row = db_execute(
        "INSERT INTO groceries (user_id, item_name, quantity, expiry_date) "
        "VALUES (%s, %s, %s, %s) "
        "RETURNING id, item_name, quantity, expiry_date, status, created_at",
        (current_user_id(), name, qty, exp),
        fetch="one",
    )
    return jsonify(item=_serialize_grocery(row)), 201


@app.get("/api/groceries/<int:gid>")
@login_required
def api_groceries_get(gid):
    row = db_execute(
        "SELECT id, item_name, quantity, expiry_date, status, created_at "
        "FROM groceries WHERE id=%s AND user_id=%s",
        (gid, current_user_id()),
        fetch="one",
    )
    if not row:
        return fail("not found", 404)
    return jsonify(item=_serialize_grocery(row))


@app.patch("/api/groceries/<int:gid>")
@login_required
def api_groceries_update(gid):
    csrf_protect()
    data = json_body()
    if not data:
        return fail("invalid JSON body")

    row = db_execute(
        "SELECT id FROM groceries WHERE id=%s AND user_id=%s",
        (gid, current_user_id()),
        fetch="one",
    )
    if not row:
        return fail("not found", 404)

    sets, params = [], []
    if "item_name" in data:
        name = (data["item_name"] or "").strip()
        if not name or len(name) > 150:
            return fail("item_name must be 1-150 chars")
        sets.append("item_name=%s"); params.append(name)
    if "quantity" in data:
        try:
            params.append(_parse_qty(data["quantity"]))
        except ValueError as e:
            return fail(str(e))
        sets.append("quantity=%s")
    if "expiry_date" in data:
        try:
            params.append(_parse_date(data["expiry_date"]))
        except ValueError as e:
            return fail(str(e))
        sets.append("expiry_date=%s")
    if "status" in data:
        if data["status"] not in VALID_STATUS:
            return fail("status must be active, used, or expired")
        sets.append("status=%s"); params.append(data["status"])

    if not sets:
        return fail("no updatable fields supplied")

    params.extend([gid, current_user_id()])
    updated = db_execute(
        f"UPDATE groceries SET {', '.join(sets)} "
        f"WHERE id=%s AND user_id=%s "
        "RETURNING id, item_name, quantity, expiry_date, status, created_at",
        tuple(params),
        fetch="one",
    )
    return jsonify(item=_serialize_grocery(updated))


@app.delete("/api/groceries/<int:gid>")
@login_required
def api_groceries_delete(gid):
    csrf_protect()
    rc = db_execute(
        "DELETE FROM groceries WHERE id=%s AND user_id=%s",
        (gid, current_user_id()),
    )
    if rc == 0:
        return fail("not found", 404)
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# Shopping-list API
# --------------------------------------------------------------------------- #
def _serialize_shop(r):
    return {
        "id": r["id"],
        "item_name": r["item_name"],
        "quantity": r["quantity"],
        "is_purchased": r["is_purchased"],
        "created_at": r["created_at"].isoformat(),
    }


@app.get("/api/shopping-items")
@login_required
def api_shop_list():
    rows = db_execute(
        "SELECT id, item_name, quantity, is_purchased, created_at "
        "FROM shopping_items WHERE user_id=%s ORDER BY created_at DESC",
        (current_user_id(),),
        fetch="all",
    )
    items = [_serialize_shop(r) for r in rows]
    remaining = sum(1 for i in items if not i["is_purchased"])
    purchased = sum(1 for i in items if i["is_purchased"])
    return jsonify(items=items, remaining=remaining, purchased=purchased)


@app.post("/api/shopping-items")
@login_required
def api_shop_create():
    csrf_protect()
    data = json_body()
    if not data:
        return fail("invalid JSON body")
    name = (data.get("item_name") or "").strip()
    if not name or len(name) > 150:
        return fail("item_name is required (max 150 chars)")
    try:
        qty = _parse_qty(data.get("quantity", 1))
    except ValueError as e:
        return fail(str(e))

    row = db_execute(
        "INSERT INTO shopping_items (user_id, item_name, quantity) "
        "VALUES (%s, %s, %s) "
        "RETURNING id, item_name, quantity, is_purchased, created_at",
        (current_user_id(), name, qty),
        fetch="one",
    )
    return jsonify(item=_serialize_shop(row)), 201


@app.patch("/api/shopping-items/<int:sid>")
@login_required
def api_shop_update(sid):
    csrf_protect()
    data = json_body()
    if not data:
        return fail("invalid JSON body")

    existing = db_execute(
        "SELECT id FROM shopping_items WHERE id=%s AND user_id=%s",
        (sid, current_user_id()),
        fetch="one",
    )
    if not existing:
        return fail("not found", 404)

    sets, params = [], []
    if "item_name" in data:
        name = (data["item_name"] or "").strip()
        if not name or len(name) > 150:
            return fail("item_name must be 1-150 chars")
        sets.append("item_name=%s"); params.append(name)
    if "quantity" in data:
        try:
            params.append(_parse_qty(data["quantity"]))
        except ValueError as e:
            return fail(str(e))
        sets.append("quantity=%s")
    if "is_purchased" in data:
        if not isinstance(data["is_purchased"], bool):
            return fail("is_purchased must be boolean")
        sets.append("is_purchased=%s"); params.append(data["is_purchased"])

    if not sets:
        return fail("no updatable fields supplied")

    params.extend([sid, current_user_id()])
    updated = db_execute(
        f"UPDATE shopping_items SET {', '.join(sets)} "
        f"WHERE id=%s AND user_id=%s "
        "RETURNING id, item_name, quantity, is_purchased, created_at",
        tuple(params),
        fetch="one",
    )
    return jsonify(item=_serialize_shop(updated))


@app.delete("/api/shopping-items/<int:sid>")
@login_required
def api_shop_delete(sid):
    csrf_protect()
    rc = db_execute(
        "DELETE FROM shopping_items WHERE id=%s AND user_id=%s",
        (sid, current_user_id()),
    )
    if rc == 0:
        return fail("not found", 404)
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# Error handlers (never leak stack traces)
# --------------------------------------------------------------------------- #
@app.errorhandler(404)
def h404(e):
    if request.path.startswith("/api/"):
        return jsonify(error="not found"), 404
    return redirect(url_for("index"))


@app.errorhandler(413)
def h413(e):
    return jsonify(error="payload too large"), 413


@app.errorhandler(Exception)
def h500(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    log.exception("unhandled error")
    if request.path.startswith("/api/"):
        return jsonify(error="server error"), 500
    return "Server error", 500


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    debug = not IS_PROD
    app.run(host="127.0.0.1", port=5000, debug=debug)