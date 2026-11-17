"""Watch list: a tiny database-backed web app.

Everything is configured from environment variables so the same image runs
locally (docker compose + Postgres container) and on EC2 (RDS PostgreSQL).
"""
import logging
import os
import time

import psycopg
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("watchlist")

KINDS = ("movie", "show")
MAX_TITLE = 200

app = Flask(__name__)


def db_conninfo() -> dict:
    return {
        "host": os.environ.get("DB_HOST", "localhost"),
        "port": int(os.environ.get("DB_PORT", "5432")),
        "dbname": os.environ.get("DB_NAME", "watchlist"),
        "user": os.environ.get("DB_USER", "watchlist"),
        "password": os.environ.get("DB_PASSWORD", ""),
        # RDS PostgreSQL forces SSL by default; "prefer" works locally and on RDS.
        # For production use "verify-full" with the RDS CA bundle.
        "sslmode": os.environ.get("DB_SSLMODE", "prefer"),
        "connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "3")),
        "application_name": "watchlist",
    }


def get_conn():
    return psycopg.connect(**db_conninfo())


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS items (
    id         SERIAL PRIMARY KEY,
    title      TEXT NOT NULL CHECK (char_length(title) BETWEEN 1 AND {MAX_TITLE}),
    kind       TEXT NOT NULL CHECK (kind IN ('movie', 'show')),
    watched    BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def init_db(retries: int = 10, delay: float = 3.0) -> None:
    """Create the table, retrying while the database comes up.

    If the database never becomes reachable we raise, the process exits, and
    Docker's restart policy tries again -- a loud failure in the logs is better
    than a container that looks "up" but can't do anything.
    """
    for attempt in range(1, retries + 1):
        try:
            with get_conn() as conn:
                conn.execute(SCHEMA)
            log.info("database ready (attempt %d)", attempt)
            return
        except psycopg.OperationalError as exc:
            log.warning("database not ready (attempt %d/%d): %s", attempt, retries, exc)
            if attempt == retries:
                raise
            time.sleep(delay)


@app.get("/")
def index():
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, title, kind, watched FROM items "
                "ORDER BY watched ASC, created_at DESC"
            ).fetchall()
    except psycopg.Error:
        log.exception("failed to load items")
        return render_template("index.html", items=[], db_error=True), 503
    items = [dict(id=r[0], title=r[1], kind=r[2], watched=r[3]) for r in rows]
    return render_template("index.html", items=items, db_error=False)


@app.post("/items")
def add_item():
    title = (request.form.get("title") or "").strip()
    kind = request.form.get("kind", "movie")
    if not title or len(title) > MAX_TITLE or kind not in KINDS:
        abort(400)
    with get_conn() as conn:
        conn.execute("INSERT INTO items (title, kind) VALUES (%s, %s)", (title, kind))
    log.info("added item kind=%s", kind)
    return redirect(url_for("index"), code=303)


@app.post("/items/<int:item_id>/toggle")
def toggle_item(item_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE items SET watched = NOT watched WHERE id = %s", (item_id,))
    return redirect(url_for("index"), code=303)


@app.post("/items/<int:item_id>/delete")
def delete_item(item_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM items WHERE id = %s", (item_id,))
    return redirect(url_for("index"), code=303)


@app.get("/health")
def health():
    """Reports app + database status. 200 = healthy, 503 = database unreachable.

    Error details go to the logs, not the public response.
    """
    started = time.perf_counter()
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
    except psycopg.Error:
        log.exception("health check: database unreachable")
        return jsonify(status="error", app="ok", database="unreachable"), 503
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return jsonify(
        status="ok",
        app="ok",
        database="ok",
        db_latency_ms=latency_ms,
        version=os.environ.get("APP_VERSION", "dev"),
    )


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp
