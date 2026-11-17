"""Integration tests against a real Postgres (CI starts one; locally use docker compose)."""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import app as watchlist  # noqa: E402


@pytest.fixture(scope="module")
def client():
    watchlist.init_db(retries=5, delay=1)
    watchlist.app.config["TESTING"] = True
    return watchlist.app.test_client()


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json()["database"] == "ok"


def test_add_toggle_delete_roundtrip(client):
    title = f"Test movie {uuid.uuid4().hex[:8]}"
    assert client.post("/items", data={"title": title, "kind": "movie"}).status_code == 303
    assert title in client.get("/").get_data(as_text=True)

    with watchlist.get_conn() as conn:
        item_id, watched = conn.execute(
            "SELECT id, watched FROM items WHERE title = %s", (title,)
        ).fetchone()
    assert watched is False

    client.post(f"/items/{item_id}/toggle")
    with watchlist.get_conn() as conn:
        assert conn.execute("SELECT watched FROM items WHERE id = %s", (item_id,)).fetchone()[0] is True

    client.post(f"/items/{item_id}/delete")
    assert title not in client.get("/").get_data(as_text=True)


def test_html_is_escaped(client):
    client.post("/items", data={"title": "<script>alert(1)</script>", "kind": "show"})
    body = client.get("/").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


@pytest.mark.parametrize("data", [
    {"title": "", "kind": "movie"},
    {"title": "x" * 201, "kind": "movie"},
    {"title": "ok", "kind": "podcast"},
])
def test_rejects_bad_input(client, data):
    assert client.post("/items", data=data).status_code == 400


def test_health_reports_db_down(client, monkeypatch):
    monkeypatch.setenv("DB_PORT", "1")  # nothing listens here
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body == {"status": "error", "app": "ok", "database": "unreachable"}
