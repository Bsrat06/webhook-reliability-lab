"""Slice 4: analytics from persisted records, exports, workspace reset and deletion."""

import csv
import io
import json

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine
from app.main import app
from tests.conftest import run_worker_until


def _two_endpoints(c) -> tuple[str, str]:
    ok = c.get("/api/v1/endpoints").json()[0]["id"]
    bad = c.post(
        "/api/v1/endpoints",
        json={
            "name": "Failing",
            "receiver_config": {"mode": "fail", "failure_status": 500},
            "retry_policy": {"max_attempts": 2, "initial_delay_ms": 100, "max_delay_ms": 100, "jitter": "none"},
        },
    ).json()["endpoint"]["id"]
    return ok, bad


def _send(c, endpoint_ids, **extra) -> str:
    r = c.post("/api/v1/events", json={"event_type": "order.created", "payload": {"n": 1}, "endpoint_ids": endpoint_ids, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _settle(c, ids):
    run_worker_until(lambda: all(c.get(f"/api/v1/events/{i}").json()["status"] != "in_progress" for i in ids))


def test_analytics_empty_workspace_is_unavailable_not_zero(guest):
    a = guest.get("/api/v1/analytics/overview?window=24h").json()
    assert a["deliveries"]["total"] == 0 and a["attempts"] == 0
    assert a["success_rate"] is None and a["retry_rate"] is None
    assert a["latency_ms"]["insufficient"] is True and a["volume"] == []
    assert "success_rate" in a["definitions"]


def test_analytics_match_persisted_records(guest, live_server):
    ok, bad = _two_endpoints(guest)
    ids = [_send(guest, [ok]), _send(guest, [ok]), _send(guest, [bad])]
    _settle(guest, ids)
    a = guest.get("/api/v1/analytics/overview?window=1h").json()
    assert a["deliveries"]["by_status"] == {"succeeded": 2, "dead_lettered": 1}
    assert a["attempts"] == 4  # 1 + 1 + 2 attempts on the failing endpoint
    assert a["success_rate"] == round(2 / 3, 4) and a["dead_letter_rate"] == round(1 / 3, 4)
    assert a["retry_rate"] == round(1 / 3, 4)
    assert a["failure_categories"] == {"success": 2, "http_5xx": 2}
    assert a["attempt_distribution"] == {"1": 2, "2": 1}
    by_name = {e["name"]: e for e in a["endpoints"]}
    assert by_name["Failing"] == {**by_name["Failing"], "attempts": 2, "failed_attempts": 2, "dead_lettered": 1}
    assert sum(v["success"] + v["failure"] for v in a["volume"]) == 4
    # Source filter: none of these came from a simulation.
    assert guest.get("/api/v1/analytics/overview?source=simulation").json()["deliveries"]["total"] == 0


def test_exports_csv_and_json_are_correct_and_safe(guest, live_server):
    ok, bad = _two_endpoints(guest)
    ids = [_send(guest, [ok], idempotency_key="=HYPERLINK(\"http://x\")"), _send(guest, [bad])]
    _settle(guest, ids)

    r = guest.get("/api/v1/exports/events?format=csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and r.headers["x-export-rows"] == "2"
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert {row["id"] for row in rows} == set(ids)
    keys = {row["idempotency_key"] for row in rows}
    assert "'=HYPERLINK(\"http://x\")" in keys  # formula neutralised
    assert json.loads(rows[0]["payload"]) == {"n": 1}

    attempts = guest.get("/api/v1/exports/attempts?format=json").json()
    assert attempts["row_count"] == 3 and attempts["truncated"] is False
    assert {a["receiver_result"] for a in attempts["rows"]} == {"processed", "rejected"}

    dl = guest.get("/api/v1/exports/dead_letters?format=json").json()
    assert dl["row_count"] == 1 and dl["rows"][0]["endpoint_name"] == "Failing"

    for dataset in ("events", "deliveries", "attempts", "dead_letters", "simulations", "analytics"):
        body = guest.get(f"/api/v1/exports/{dataset}?format=json").text
        assert "whsec_" not in body and "x-webhook-signature" not in body and "secret" not in body.lower(), dataset
    assert any(a["action"] == "data.exported" for a in guest.get("/api/v1/workspaces/current/audit").json())


def test_export_is_bounded_and_flags_truncation(guest):
    ok = guest.get("/api/v1/endpoints").json()[0]["id"]
    for _ in range(3):
        _send(guest, [ok])
    settings = get_settings()
    original = settings.quota_export_rows
    settings.quota_export_rows = 2
    try:
        r = guest.get("/api/v1/exports/events?format=csv")
        assert r.headers["x-export-truncated"] == "true" and r.headers["x-export-rows"] == "2"
        assert len(list(csv.DictReader(io.StringIO(r.text)))) == 2
        assert guest.get("/api/v1/exports/events?format=json").json()["truncated"] is True
    finally:
        settings.quota_export_rows = original


def test_export_and_analytics_are_workspace_scoped(guest):
    _send(guest, [guest.get("/api/v1/endpoints").json()[0]["id"]])
    with TestClient(app, headers={"x-requested-with": "fetch"}) as other:
        other.post("/api/v1/guest/session")
        assert other.get("/api/v1/exports/events?format=json").json()["row_count"] == 0
        assert other.get("/api/v1/exports/deliveries?format=json").json()["row_count"] == 0
        assert other.get("/api/v1/analytics/overview?window=all").json()["deliveries"]["total"] == 0
    assert TestClient(app).get("/api/v1/exports/events").status_code == 401


def test_workspace_reset_requires_confirmation_and_clears_data(guest, live_server):
    ok, bad = _two_endpoints(guest)
    _settle(guest, [_send(guest, [ok, bad])])
    old_ids = {ok, bad}
    assert guest.post("/api/v1/workspaces/current/reset", json={"confirm": "yes"}).status_code == 422
    r = guest.post("/api/v1/workspaces/current/reset", json={"confirm": "reset"})
    assert r.status_code == 200 and r.json()["usage"]["endpoints"] == 1
    assert guest.get("/api/v1/events").json()["items"] == []
    assert guest.get("/api/v1/dead-letters?state=all").json()["items"] == []
    (ep,) = guest.get("/api/v1/endpoints").json()
    assert ep["id"] not in old_ids
    assert guest.get("/api/v1/workspaces/current/audit").json()[0]["action"] == "workspace.reset"
    ws = guest.get("/api/v1/session").json()["workspace"]["id"]
    with engine.connect() as conn:
        for table in ("deliveries", "delivery_attempts", "jobs", "idempotency_records"):
            assert conn.execute(text(f"SELECT count(*) FROM {table} WHERE workspace_id = :w"), {"w": ws}).scalar_one() == 0, table


def test_delete_workspace_now(guest):
    ws = guest.get("/api/v1/session").json()["workspace"]["id"]
    assert guest.post("/api/v1/workspaces/current/delete", json={"confirm": "nope"}).status_code == 422
    r = guest.post("/api/v1/workspaces/current/delete", json={"confirm": "delete"})
    assert r.status_code == 204
    assert 'wh_session=""' in r.headers.get("set-cookie", "") or "Max-Age=0" in r.headers.get("set-cookie", "")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM workspaces WHERE id = :w"), {"w": ws}).scalar_one() == 0
    assert guest.get("/api/v1/session").status_code == 401
