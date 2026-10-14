"""Reconciliation System - accounts, file uploads and reconciliation runs.

Run:  python app.py   then open http://127.0.0.1:5000
"""
import csv
import io
import json
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from werkzeug.middleware.proxy_fix import ProxyFix
from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import reconcile as engine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("RECON_DATA_DIR", os.path.join(BASE_DIR, "data"))
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
DB_PATH = os.path.join(DATA_DIR, "recon.db")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB per request
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Set COOKIE_SECURE=1 when served over HTTPS, TRUST_PROXY=1 when behind a reverse proxy.
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE") == "1"
if os.environ.get("TRUST_PROXY") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

MAX_ROWS = 50_000  # per file, keeps one user from tying up the server
ALLOW_REGISTRATION = os.environ.get("ALLOW_REGISTRATION", "1") == "1"
LOGIN_MAX_FAILS = 5          # per username+IP in the window
LOGIN_MAX_FAILS_IP = 25      # per IP in the window
LOGIN_WINDOW_MIN = 15


def _secret_key():
    """Use SECRET_KEY from the environment, otherwise generate and persist one."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(DATA_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
        os.chmod(path, 0o600)
    return open(path).read().strip()


app.secret_key = _secret_key()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    uploaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    file_a_name TEXT NOT NULL,
    file_b_name TEXT NOT NULL,
    settings TEXT NOT NULL,
    summary TEXT NOT NULL,
    results TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    username TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fail_ip ON login_failures(ip, at);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=15)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


init_db()


# ----------------------------------------------------------------- helpers
def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def load_user_and_check_csrf():
    g.user = None
    if session.get("user_id"):
        g.user = db().execute("SELECT * FROM users WHERE id = ?", (session["user_id"],)).fetchone()
        if g.user is None:
            session.clear()
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, "Invalid or missing form token. Go back, refresh the page and try again.")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    if request.endpoint != "static":
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.route("/healthz")
def healthz():
    db().execute("SELECT 1").fetchone()
    return "ok", 200, {"Content-Type": "text/plain"}


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if g.user is None:
            flash("Please log in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapped


def own_file(file_id):
    row = db().execute("SELECT * FROM files WHERE id = ? AND user_id = ?", (file_id, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    return row


def file_path(row):
    return os.path.join(UPLOAD_DIR, row["stored_name"])


def money(v):
    return f"{v:,.2f}"


app.jinja_env.filters["money"] = money


@app.errorhandler(413)
def too_large(_e):
    flash("That file is too large (limit 10 MB).", "error")
    return redirect(url_for("files"))


# -------------------------------------------------------------------- auth
@app.route("/")
def index():
    return redirect(url_for("dashboard") if g.user else url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if not ALLOW_REGISTRATION:
        flash("New sign-ups are closed. Ask the administrator for an account.", "info")
        return redirect(url_for("login"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        error = None
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            error = "Username must be 3-32 characters: letters, numbers, dot, dash or underscore."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        elif db().execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            error = "That username is already taken."
        if error:
            flash(error, "error")
            return render_template("register.html", username=username), 400
        cur = db().execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                           (username, generate_password_hash(password), now()))
        db().commit()
        session.clear()
        session["user_id"] = cur.lastrowid
        flash("Account created. Welcome!", "success")
        return redirect(url_for("dashboard"))
    return render_template("register.html", username="")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        ip = request.remote_addr or "unknown"
        since = (datetime.now(timezone.utc) - timedelta(minutes=LOGIN_WINDOW_MIN)).strftime("%Y-%m-%d %H:%M:%S")
        db().execute("DELETE FROM login_failures WHERE at < ?", (since,))
        fails_user = db().execute("SELECT COUNT(*) FROM login_failures WHERE ip = ? AND username = ? AND at >= ?",
                                  (ip, username.lower(), since)).fetchone()[0]
        fails_ip = db().execute("SELECT COUNT(*) FROM login_failures WHERE ip = ? AND at >= ?", (ip, since)).fetchone()[0]
        if fails_user >= LOGIN_MAX_FAILS or fails_ip >= LOGIN_MAX_FAILS_IP:
            flash(f"Too many failed attempts. Try again in {LOGIN_WINDOW_MIN} minutes.", "error")
            return render_template("login.html", username=username), 429
        user = db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            db().execute("DELETE FROM login_failures WHERE ip = ? AND username = ?", (ip, username.lower()))
            db().commit()
            session.clear()
            session["user_id"] = user["id"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
        db().execute("INSERT INTO login_failures (ip, username, at) VALUES (?,?,?)",
                     (ip, username.lower(), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
        db().commit()
        flash("Incorrect username or password.", "error")
        return render_template("login.html", username=username), 401
    return render_template("login.html", username="")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# --------------------------------------------------------------- dashboard
@app.route("/dashboard")
@login_required
def dashboard():
    runs = db().execute(
        "SELECT id, name, file_a_name, file_b_name, summary, created_at FROM reconciliations "
        "WHERE user_id = ? ORDER BY id DESC LIMIT 10", (g.user["id"],)).fetchall()
    nfiles = db().execute("SELECT COUNT(*) FROM files WHERE user_id = ?", (g.user["id"],)).fetchone()[0]
    return render_template("dashboard.html", runs=[dict(r, summary=json.loads(r["summary"])) for r in runs], nfiles=nfiles)


# ------------------------------------------------------------------- files
@app.route("/files")
@login_required
def files():
    rows = db().execute("SELECT * FROM files WHERE user_id = ? ORDER BY id DESC", (g.user["id"],)).fetchall()
    return render_template("files.html", files=rows)


@app.route("/files/upload", methods=["POST"])
@login_required
def upload():
    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not uploads:
        flash("Choose at least one file to upload.", "error")
        return redirect(url_for("files"))
    for f in uploads:
        name = os.path.basename(f.filename)
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ("csv", "xlsx", "xlsm"):
            flash(f"{name}: only .csv and .xlsx files are supported.", "error")
            continue
        stored = uuid.uuid4().hex + "." + ext
        path = os.path.join(UPLOAD_DIR, stored)
        f.save(path)
        try:
            _headers, rows = engine.read_table(path, name)
        except engine.ParseError as exc:
            os.remove(path)
            flash(f"{name}: {exc}", "error")
            continue
        db().execute("INSERT INTO files (user_id, original_name, stored_name, row_count, uploaded_at) VALUES (?,?,?,?,?)",
                     (g.user["id"], name, stored, len(rows), now()))
        db().commit()
        flash(f"Uploaded {name} ({len(rows)} rows).", "success")
    return redirect(url_for("files"))


@app.route("/files/<int:file_id>/delete", methods=["POST"])
@login_required
def delete_file(file_id):
    row = own_file(file_id)
    try:
        os.remove(file_path(row))
    except FileNotFoundError:
        pass
    db().execute("DELETE FROM files WHERE id = ?", (file_id,))
    db().commit()
    flash(f"Deleted {row['original_name']}.", "info")
    return redirect(url_for("files"))


@app.route("/files/<int:file_id>/preview")
@login_required
def preview(file_id):
    row = own_file(file_id)
    try:
        headers, rows = engine.read_table(file_path(row), row["original_name"])
    except engine.ParseError as exc:
        flash(str(exc), "error")
        return redirect(url_for("files"))
    return render_template("preview.html", file=row, headers=headers, rows=rows[:50], total=len(rows))


# ---------------------------------------------------------- reconciliation
@app.route("/reconcile/new")
@login_required
def new_reconciliation():
    rows = db().execute("SELECT * FROM files WHERE user_id = ? ORDER BY id DESC", (g.user["id"],)).fetchall()
    if len(rows) < 1:
        flash("Upload at least one file first (you can reconcile a file against another file).", "info")
        return redirect(url_for("files"))
    return render_template("reconcile_new.html", files=rows)


@app.route("/reconcile/configure", methods=["POST"])
@login_required
def configure():
    try:
        a = own_file(int(request.form.get("file_a", "0")))
        b = own_file(int(request.form.get("file_b", "0")))
    except ValueError:
        abort(400)
    if a["id"] == b["id"]:
        flash("Choose two different files.", "error")
        return redirect(url_for("new_reconciliation"))
    try:
        ha, _ = engine.read_table(file_path(a), a["original_name"])
        hb, _ = engine.read_table(file_path(b), b["original_name"])
    except engine.ParseError as exc:
        flash(str(exc), "error")
        return redirect(url_for("new_reconciliation"))
    return render_template("reconcile_configure.html", a=a, b=b, ha=ha, hb=hb,
                           ga=engine.guess_columns(ha), gb=engine.guess_columns(hb))


def _float(name, default, lo, hi):
    try:
        return min(max(float(request.form.get(name, default)), lo), hi)
    except ValueError:
        return default


@app.route("/reconcile/run", methods=["POST"])
@login_required
def run():
    try:
        a = own_file(int(request.form.get("file_a", "0")))
        b = own_file(int(request.form.get("file_b", "0")))
    except ValueError:
        abort(400)
    map_a = {f: request.form.get(f"a_{f}", "") for f in ("amount", "date", "reference", "description")}
    map_b = {f: request.form.get(f"b_{f}", "") for f in ("amount", "date", "reference", "description")}
    if map_a["amount"] == "" or map_b["amount"] == "":
        flash("Choose the amount column for both files.", "error")
        return redirect(url_for("new_reconciliation"))
    tolerance = _float("tolerance", 0.01, 0, 1_000_000)
    window = int(_float("date_window", 3, 0, 365))
    fuzzy = request.form.get("fuzzy") == "on"
    flip = request.form.get("flip_b") == "on"  # B uses opposite sign convention

    try:
        ha, ra = engine.read_table(file_path(a), a["original_name"])
        hb, rb = engine.read_table(file_path(b), b["original_name"])
    except engine.ParseError as exc:
        flash(str(exc), "error")
        return redirect(url_for("new_reconciliation"))
    recs_a, skipped_a = engine.build_records(ha, ra, map_a)
    recs_b, skipped_b = engine.build_records(hb, rb, map_b)
    if len(recs_a) > MAX_ROWS or len(recs_b) > MAX_ROWS:
        flash(f"Each file can have at most {MAX_ROWS:,} usable rows. Split the file by month and reconcile each part.", "error")
        return redirect(url_for("new_reconciliation"))
    if flip:
        for r in recs_b:
            r["amount"] = -r["amount"]
    if not recs_a or not recs_b:
        flash("No usable rows found - check that the amount columns are correct.", "error")
        return redirect(url_for("new_reconciliation"))

    result = engine.reconcile(recs_a, recs_b, tolerance=tolerance, date_window=window, fuzzy=fuzzy)
    result["summary"]["skipped_a"] = skipped_a
    result["summary"]["skipped_b"] = skipped_b
    name = request.form.get("name", "").strip() or f"{a['original_name']} vs {b['original_name']}"
    settings = {"tolerance": tolerance, "date_window": window, "fuzzy": fuzzy, "flip_b": flip}
    cur = db().execute(
        "INSERT INTO reconciliations (user_id, name, file_a_name, file_b_name, settings, summary, results, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (g.user["id"], name[:120], a["original_name"], b["original_name"], json.dumps(settings),
         json.dumps(result["summary"]), json.dumps(result), now()))
    db().commit()
    return redirect(url_for("result", rec_id=cur.lastrowid))


def own_run(rec_id):
    row = db().execute("SELECT * FROM reconciliations WHERE id = ? AND user_id = ?", (rec_id, g.user["id"])).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/reconciliations")
@login_required
def history():
    rows = db().execute(
        "SELECT id, name, file_a_name, file_b_name, summary, created_at FROM reconciliations "
        "WHERE user_id = ? ORDER BY id DESC", (g.user["id"],)).fetchall()
    return render_template("history.html", runs=[dict(r, summary=json.loads(r["summary"])) for r in rows])


@app.route("/reconciliations/<int:rec_id>")
@login_required
def result(rec_id):
    row = own_run(rec_id)
    return render_template("result.html", run=row, res=json.loads(row["results"]),
                           settings=json.loads(row["settings"]))


@app.route("/reconciliations/<int:rec_id>/export.csv")
@login_required
def export(rec_id):
    row = own_run(rec_id)
    res = json.loads(row["results"])
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["status", "note", "difference",
                "a_row", "a_date", "a_reference", "a_description", "a_amount",
                "b_row", "b_date", "b_reference", "b_description", "b_amount"])

    def side(r):
        return [r["row"], r["date"], r["reference"], r["description"], r["amount"]] if r else [""] * 5

    for m in res["matched"]:
        w.writerow(["matched", m["method"], 0] + side(m["a"]) + side(m["b"]))
    for d in res["discrepancies"]:
        w.writerow(["discrepancy", d["reason"], d["difference"]] + side(d["a"]) + side(d["b"]))
    for r in res["unmatched_a"]:
        w.writerow(["unmatched_a", "Only in file A", r["amount"]] + side(r) + side(None))
    for r in res["unmatched_b"]:
        w.writerow(["unmatched_b", "Only in file B", -r["amount"]] + side(None) + side(r))
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", row["name"])[:60] or "reconciliation"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{safe}.csv"'})


@app.route("/reconciliations/<int:rec_id>/delete", methods=["POST"])
@login_required
def delete_run(rec_id):
    own_run(rec_id)
    db().execute("DELETE FROM reconciliations WHERE id = ?", (rec_id,))
    db().commit()
    flash("Reconciliation deleted.", "info")
    return redirect(url_for("history"))


if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
