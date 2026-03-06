"""Slice 2: idempotency, failure modes, DLQ, replay lineage, cancellation, concurrency limits, retention."""

import threading
import uuid
from datetime import datetime

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.security import sign_payload
from app.db.session import SessionLocal, engine
from app.main import app
from app.services.retention import run_cleanup, schedule_cleanup
from app.workers.delivery import claim_jobs
from tests.conftest import run_worker_until


def _ep(c) -> dict:
    return c.get("/api/v1/endpoints").json()[0]


def _configure(c, ep_id, receiver=None, policy=None):
    body = {}
    if receiver is not None:
        body["receiver_config"] = receiver
    if policy is not None:
        body["retry_policy"] = {"initial_delay_ms": 100, "max_delay_ms": 200, "jitter": "none", **policy}
    r = c.patch(f"/api/v1/endpoints/{ep_id}", json=body)
    assert r.status_code == 200, r.text


def _send(c, ep_id, **extra) -> str:
    r = c.post("/api/v1/events", json={"event_type": "order.created", "payload": {"n": 1}, "endpoint_ids": [ep_id], **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _event(c, event_id) -> dict:
    return c.get(f"/api/v1/events/{event_id}").json()


def _finished(c, event_id):
    return lambda: _event(c, event_id)["status"] not in ("in_progress",)


# ---- Idempotency ---------------------------------------------------------------------------------------


def test_concurrent_duplicate_requests_processed_exactly_once(guest, live_server):
    created = guest.post("/api/v1/endpoints", json={"name": "idem"}).json()
    ep_id, secret = created["endpoint"]["id"], created["signing_secret"]
    body = b'{"id":"evt_1","type":"t","data":{}}'
    results: list[str] = []
    barrier = threading.Barrier(16)

    def hit():
        headers = {
            "x-webhook-signature": sign_payload(secret, body),
            "idempotency-key": "order-42",
            "x-webhook-event-id": str(uuid.uuid4()),
            "content-type": "application/json",
        }
        barrier.wait()
        r = httpx.post(f"{live_server}/receiver/{ep_id}", content=body, headers=headers, timeout=10)
        results.append(r.headers["x-receiver-result"])

    threads = [threading.Thread(target=hit) for _ in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["duplicate"] * 15 + ["processed"]
    (rec,) = guest.get(f"/api/v1/endpoints/{ep_id}/idempotency-records").json()
    assert rec["idempotency_key"] == "order-42" and rec["seen_count"] == 16


def test_idempotency_unique_constraint_enforced_by_database(guest):
    ep = _ep(guest)
    ws_id = guest.get("/api/v1/session").json()["workspace"]["id"]
    insert = text(
        "INSERT INTO idempotency_records (id, workspace_id, endpoint_id, idempotency_key, seen_count, "
        "processing_result, expires_at) VALUES (gen_random_uuid(), :ws, :ep, 'k', 1, 'processed', now())"
    )
    with engine.begin() as conn:
        conn.execute(insert, {"ws": ws_id, "ep": ep["id"]})
    try:
        with engine.begin() as conn:
            conn.execute(insert, {"ws": ws_id, "ep": ep["id"]})
        raise AssertionError("duplicate key was accepted")
    except Exception as exc:  # noqa: BLE001
        assert "uq_idempotency_scope" in str(exc)


def test_same_idempotency_key_on_second_event_is_suppressed(guest, live_server):
    ep = _ep(guest)
    first = _send(guest, ep["id"], idempotency_key="charge-7")
    second = _send(guest, ep["id"], idempotency_key="charge-7")
    run_worker_until(lambda: all(_event(guest, e)["status"] == "delivered" for e in (first, second)))
    results = sorted(_event(guest, e)["attempts"][0]["response_headers"]["x-receiver-result"] for e in (first, second))
    assert results == ["duplicate", "processed"]


def test_lost_acknowledgement_causes_visible_duplicate(guest, live_server):
    ep = _ep(guest)
    _configure(guest, ep["id"], receiver={"mode": "succeed", "ack_loss_rate": 1.0}, policy={"max_attempts": 2})
    ev = _send(guest, ep["id"], idempotency_key="ship-1")
    run_worker_until(lambda: _event(guest, ev)["status"] == "failed")
    attempts = _event(guest, ev)["attempts"]
    assert [a["error_category"] for a in attempts] == ["connection_reset", "connection_reset"]
    (rec,) = guest.get(f"/api/v1/endpoints/{ep['id']}/idempotency-records").json()
    assert rec["seen_count"] == 2  # processed once, received twice


# ---- Failure modes -------------------------------------------------------------------------------------


def test_timeout_is_recorded_as_timeout(guest, live_server):
    ep = _ep(guest)
    _configure(guest, ep["id"], receiver={"mode": "fail", "failure_kind": "timeout"}, policy={"max_attempts": 1, "timeout_ms": 300})
    ev = _send(guest, ep["id"])
    run_worker_until(lambda: _event(guest, ev)["status"] == "failed")
    (a,) = _event(guest, ev)["attempts"]
    assert a["error_category"] == "timeout" and a["response_status"] is None
    assert 250 <= a["duration_ms"] < 2000


def test_connection_drop_is_a_network_failure(guest, live_server):
    ep = _ep(guest)
    _configure(guest, ep["id"], receiver={"mode": "fail_first_n", "fail_first_n": 1, "failure_kind": "connection_drop"}, policy={"max_attempts": 3})
    ev = _send(guest, ep["id"])
    run_worker_until(lambda: _event(guest, ev)["status"] == "delivered")
    attempts = _event(guest, ev)["attempts"]
    assert [a["error_category"] for a in attempts] == ["connection_reset", None]


def test_rate_limit_retry_after_is_respected(guest, live_server):
    ep = _ep(guest)
    _configure(
        guest,
        ep["id"],
        receiver={"mode": "fail_first_n", "fail_first_n": 1, "failure_kind": "rate_limited", "retry_after_s": 1},
        policy={"max_attempts": 3, "max_delay_ms": 5000},
    )
    ev = _send(guest, ep["id"])
    run_worker_until(lambda: _event(guest, ev)["status"] == "delivered")
    a1, a2 = _event(guest, ev)["attempts"]
    assert a1["response_status"] == 429 and a1["response_headers"]["retry-after"] == "1"
    gap = datetime.fromisoformat(a2["request_started_at"]) - datetime.fromisoformat(a1["request_completed_at"])
    assert gap.total_seconds() >= 0.95


# ---- Dead-letter queue and replay --------------------------------------------------------------------


def _dead_letter_one(c) -> tuple[str, dict]:
    ep = _ep(c)
    _configure(c, ep["id"], receiver={"mode": "fail", "failure_status": 500}, policy={"max_attempts": 1})
    ev = _send(c, ep["id"])
    run_worker_until(lambda: _event(c, ev)["status"] == "failed")
    return ev, ep


def test_dead_letter_inspect_replay_with_lineage(guest, live_server):
    ev, ep = _dead_letter_one(guest)
    listing = guest.get("/api/v1/dead-letters").json()
    assert listing["counts"] == {"open": 1, "resolved": 0}
    (item,) = listing["items"]
    assert item["last_status"] == 500 and item["event_type"] == "order.created"
    detail = guest.get(f"/api/v1/dead-letters/{item['id']}").json()
    assert detail["payload"] == {"n": 1} and len(detail["attempts"]) == 1

    _configure(guest, ep["id"], receiver={"mode": "succeed"})
    r = guest.post(f"/api/v1/dead-letters/{item['id']}/replay", json={"policy": "fast"})
    assert r.status_code == 201, r.text
    (replay,) = r.json()["replays"]
    run_worker_until(lambda: _event(guest, ev)["status"] != "in_progress")

    event = _event(guest, ev)
    original, new = event["deliveries"]
    assert original["status"] == "dead_lettered"  # history preserved
    assert new["id"] == replay["id"] and new["replay_of_delivery_id"] == original["id"]
    assert new["status"] == "succeeded" and new["retry_policy"]["max_attempts"] == 4
    assert len(event["attempts"]) == 2
    assert event["status"] == "delivered"  # judged by the replay, not the superseded original

    after = guest.get("/api/v1/dead-letters?state=resolved").json()["items"][0]
    assert after["resolution_note"].startswith("Replayed as delivery") and after["replays"][0]["id"] == new["id"]
    actions = [a["action"] for a in guest.get("/api/v1/workspaces/current/audit").json()]
    assert "delivery.replayed" in actions


def test_resolve_dead_letter_is_audited_once(guest, live_server):
    _dead_letter_one(guest)
    (item,) = guest.get("/api/v1/dead-letters").json()["items"]
    r = guest.post(f"/api/v1/dead-letters/{item['id']}/resolve", json={"note": "customer refunded manually"})
    assert r.status_code == 200 and r.json()["resolution_note"] == "customer refunded manually"
    assert guest.post(f"/api/v1/dead-letters/{item['id']}/resolve", json={}).status_code == 409
    audit = guest.get("/api/v1/workspaces/current/audit").json()
    assert audit[0]["action"] == "dead_letter.resolved" and audit[0]["actor"].startswith("guest_session:")


def test_bulk_replay_requires_confirmation_and_bounds(guest, live_server):
    ep = _ep(guest)
    _configure(guest, ep["id"], receiver={"mode": "fail", "failure_status": 500}, policy={"max_attempts": 1})
    events = [_send(guest, ep["id"]) for _ in range(3)]
    run_worker_until(lambda: all(_event(guest, e)["status"] == "failed" for e in events))
    ids = [_event(guest, e)["deliveries"][0]["id"] for e in events]

    r = guest.post("/api/v1/deliveries/replay", json={"delivery_ids": ids, "confirm_count": 2})
    assert r.status_code == 422 and r.json()["error"]["code"] == "confirmation_required"
    too_many = [str(uuid.uuid4()) for _ in range(51)]
    r = guest.post("/api/v1/deliveries/replay", json={"delivery_ids": too_many, "confirm_count": 51})
    assert r.json()["error"]["code"] == "bulk_limit"
    r = guest.post("/api/v1/deliveries/replay", json={"delivery_ids": ids, "confirm_count": 3})
    assert r.status_code == 201 and len(r.json()["replays"]) == 3
    # Replaying a delivery that is still in flight is refused.
    active = r.json()["replays"][0]["id"]
    assert guest.post(f"/api/v1/deliveries/{active}/replay", json={}).status_code == 409


def test_replay_custom_policy_requires_spec(guest, live_server):
    ev, _ = _dead_letter_one(guest)
    d = _event(guest, ev)["deliveries"][0]["id"]
    assert guest.post(f"/api/v1/deliveries/{d}/replay", json={"policy": "custom"}).json()["error"]["code"] == "policy_required"
    r = guest.post(f"/api/v1/deliveries/{d}/replay", json={"policy": "custom", "retry_policy": {"max_attempts": 9}})
    assert r.status_code == 201
    assert _event(guest, ev)["deliveries"][1]["retry_policy"]["max_attempts"] == 9


def test_cancel_scheduled_retry(guest, live_server):
    ep = _ep(guest)
    _configure(guest, ep["id"], receiver={"mode": "fail", "failure_status": 503}, policy={"max_attempts": 5, "initial_delay_ms": 60_000, "max_delay_ms": 60_000})
    ev = _send(guest, ep["id"])
    run_worker_until(lambda: _event(guest, ev)["deliveries"][0]["status"] == "retry_scheduled")
    d = _event(guest, ev)["deliveries"][0]["id"]
    r = guest.post(f"/api/v1/deliveries/{d}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    with engine.connect() as conn:
        queued = conn.execute(text("SELECT count(*) FROM jobs WHERE status = 'queued'")).scalar_one()
    assert queued == 0
    assert guest.post(f"/api/v1/deliveries/{d}/cancel").status_code == 409


def test_cross_workspace_replay_and_dlq_access_denied(guest, live_server):
    ev, ep = _dead_letter_one(guest)
    (item,) = guest.get("/api/v1/dead-letters").json()["items"]
    d = item["delivery_id"]
    with TestClient(app, headers={"x-requested-with": "fetch"}) as other:
        other.post("/api/v1/guest/session")
        assert other.get("/api/v1/dead-letters").json()["items"] == []
        assert other.get(f"/api/v1/dead-letters/{item['id']}").status_code == 404
        assert other.post(f"/api/v1/dead-letters/{item['id']}/replay", json={}).status_code == 404
        assert other.post(f"/api/v1/dead-letters/{item['id']}/resolve", json={}).status_code == 404
        assert other.post(f"/api/v1/deliveries/{d}/replay", json={}).status_code == 404
        assert other.post(f"/api/v1/deliveries/{d}/cancel").status_code == 404
        r = other.post("/api/v1/deliveries/replay", json={"delivery_ids": [d], "confirm_count": 1})
        assert r.status_code == 404
        assert other.get(f"/api/v1/endpoints/{ep['id']}/idempotency-records").status_code == 404
        assert all(a["resource_id"] != d for a in other.get("/api/v1/workspaces/current/audit").json())


# ---- Concurrency and retention -----------------------------------------------------------------------


def test_per_workspace_concurrency_limit_in_claims(guest):
    ep = _ep(guest)
    for _ in range(10):
        _send(guest, ep["id"])
    with TestClient(app, headers={"x-requested-with": "fetch"}) as other:
        other.post("/api/v1/guest/session")
        _send(other, _ep(other)["id"])
    with SessionLocal() as db:
        first = claim_jobs(db, "w1", 10)
    with SessionLocal() as db:
        second = claim_jobs(db, "w2", 10)
    with engine.connect() as conn:
        per_ws = dict(
            conn.execute(text("SELECT concurrency_key, count(*) FROM jobs WHERE status = 'running' GROUP BY 1")).all()
        )
    # Guest A is capped at 4 in flight; guest B still gets its job; nothing else is claimable.
    assert sorted(per_ws.values()) == [1, 4]
    assert len(first) == 5 and second == []


def test_retention_cleanup_deletes_expired_guest_workspaces(guest, client):
    ep = _ep(guest)
    _send(guest, ep["id"])
    with TestClient(app, headers={"x-requested-with": "fetch"}) as keeper:
        keeper.post("/api/v1/guest/session")
        keep_id = keeper.get("/api/v1/session").json()["workspace"]["id"]
        ws_id = guest.get("/api/v1/session").json()["workspace"]["id"]
        with engine.begin() as conn:
            conn.execute(text("UPDATE workspaces SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": ws_id})
        with SessionLocal() as db:
            summary = run_cleanup(db)
        assert "workspaces=1" in summary
        with engine.connect() as conn:
            for table in ("workspaces", "events", "deliveries", "jobs", "endpoints", "guest_sessions"):
                n = conn.execute(text(f"SELECT count(*) FROM {table} WHERE {'id' if table == 'workspaces' else 'workspace_id'} = :id"), {"id": ws_id}).scalar_one()
                assert n == 0, table
        assert guest.get("/api/v1/session").status_code == 401
        assert keeper.get("/api/v1/session").json()["workspace"]["id"] == keep_id


def test_cleanup_schedule_is_deduplicated():
    with SessionLocal() as db:
        schedule_cleanup(db)
        schedule_cleanup(db)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM jobs WHERE job_type = 'cleanup'")).scalar_one() == 1


def test_worker_runs_cleanup_job(guest):
    ws_id = guest.get("/api/v1/session").json()["workspace"]["id"]
    with engine.begin() as conn:
        conn.execute(text("UPDATE workspaces SET expires_at = now() - interval '1 minute' WHERE id = :id"), {"id": ws_id})

    def gone():
        with engine.connect() as conn:
            return conn.execute(text("SELECT count(*) FROM workspaces WHERE id = :id"), {"id": ws_id}).scalar_one() == 0

    run_worker_until(gone)
