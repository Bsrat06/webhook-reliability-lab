"""Slice 1: guest -> isolated workspace -> built-in receiver -> event -> durable async delivery -> attempts."""

from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import run_worker_until


def _builtin(c) -> dict:
    eps = c.get("/api/v1/endpoints").json()
    assert len(eps) == 1 and eps[0]["endpoint_type"] == "builtin"
    return eps[0]


def _send(c, ep_id, **extra):
    r = c.post(
        "/api/v1/events", json={"event_type": "order.created", "payload": {"n": 1}, "endpoint_ids": [ep_id], **extra}
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_guest_session_persists_and_has_receiver(guest):
    s1 = guest.get("/api/v1/session").json()
    again = guest.post("/api/v1/guest/session")
    assert again.status_code == 200
    assert again.json()["workspace"]["id"] == s1["workspace"]["id"]
    _builtin(guest)


def test_event_is_delivered_asynchronously_and_attempt_persisted(guest, live_server):
    ep = _builtin(guest)
    ev = _send(guest, ep["id"])
    assert ev["deliveries"][0]["status"] == "pending"
    assert ev["attempts"] == []

    def done():
        return guest.get(f"/api/v1/events/{ev['id']}").json()["status"] == "delivered"

    run_worker_until(done)
    detail = guest.get(f"/api/v1/events/{ev['id']}").json()
    (a,) = detail["attempts"]
    assert a["response_status"] == 200 and a["outcome"] == "success" and a["duration_ms"] >= 0
    assert "v1=…" in a["request_headers"]["x-webhook-signature"]
    assert [t["to_status"] for t in detail["transitions"]] == ["pending", "processing", "succeeded"]


def test_retries_then_succeeds(guest, live_server):
    ep = _builtin(guest)
    guest.patch(
        f"/api/v1/endpoints/{ep['id']}",
        json={
            "receiver_config": {"mode": "fail_first_n", "fail_first_n": 2, "failure_status": 503},
            "retry_policy": {"max_attempts": 4, "initial_delay_ms": 100, "max_delay_ms": 200, "jitter": "none"},
        },
    ).raise_for_status()
    ev = _send(guest, ep["id"])
    run_worker_until(lambda: guest.get(f"/api/v1/events/{ev['id']}").json()["status"] == "delivered")
    attempts = guest.get(f"/api/v1/events/{ev['id']}").json()["attempts"]
    assert [a["response_status"] for a in attempts] == [503, 503, 200]
    assert [a["outcome"] for a in attempts] == ["retryable_failure", "retryable_failure", "success"]


def test_exhausted_retries_dead_letter(guest, live_server):
    ep = _builtin(guest)
    guest.patch(
        f"/api/v1/endpoints/{ep['id']}",
        json={
            "receiver_config": {"mode": "fail", "failure_status": 500},
            "retry_policy": {"max_attempts": 2, "initial_delay_ms": 100, "max_delay_ms": 100, "jitter": "none"},
        },
    ).raise_for_status()
    ev = _send(guest, ep["id"])
    run_worker_until(lambda: guest.get(f"/api/v1/events/{ev['id']}").json()["status"] == "failed")
    d = guest.get(f"/api/v1/events/{ev['id']}").json()
    assert d["deliveries"][0]["status"] == "dead_lettered"
    assert "exhausted after 2" in d["deliveries"][0]["dead_letter_reason"]


def test_non_retryable_status_dead_letters_immediately(guest, live_server):
    ep = _builtin(guest)
    guest.patch(f"/api/v1/endpoints/{ep['id']}", json={"receiver_config": {"mode": "fail", "failure_status": 400}})
    ev = _send(guest, ep["id"])
    run_worker_until(lambda: guest.get(f"/api/v1/events/{ev['id']}").json()["status"] == "failed")
    assert len(guest.get(f"/api/v1/events/{ev['id']}").json()["attempts"]) == 1


def test_workspace_isolation(guest):
    ep = _builtin(guest)
    ev = _send(guest, ep["id"])
    with TestClient(app, headers={"x-requested-with": "fetch"}) as other:
        assert other.post("/api/v1/guest/session").status_code == 201
        assert other.get(f"/api/v1/events/{ev['id']}").status_code == 404
        assert other.get(f"/api/v1/endpoints/{ep['id']}").status_code == 404
        assert other.patch(f"/api/v1/endpoints/{ep['id']}", json={"name": "x"}).status_code == 404
        assert other.delete(f"/api/v1/endpoints/{ep['id']}").status_code == 404
        r = other.post("/api/v1/events", json={"event_type": "x", "payload": {}, "endpoint_ids": [ep["id"]]})
        assert r.status_code == 422
        assert other.get("/api/v1/events").json()["items"] == []


def test_unauthenticated_and_csrf(client):
    assert client.get("/api/v1/endpoints").status_code == 401
    with TestClient(app) as bare:
        assert bare.post("/api/v1/guest/session").status_code == 403
    r = client.post("/api/v1/guest/session", headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_quotas_enforced_server_side(guest):
    for i in range(4):
        assert guest.post("/api/v1/endpoints", json={"name": f"e{i}"}).status_code == 201
    r = guest.post("/api/v1/endpoints", json={"name": "too many"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"
    ep = guest.get("/api/v1/endpoints").json()[0]
    big = {"blob": "x" * (257 * 1024)}
    r = guest.post("/api/v1/events", json={"event_type": "big", "payload": big, "endpoint_ids": [ep["id"]]})
    assert r.status_code == 413


def test_external_endpoints_disabled(guest):
    r = guest.post(
        "/api/v1/endpoints", json={"name": "ext", "endpoint_type": "external", "destination_url": "https://x.io"}
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "external_disabled"


def test_receiver_rejects_unsigned(guest, live_server):
    import httpx

    ep = _builtin(guest)
    assert httpx.post(f"{live_server}/receiver/{ep['id']}", content=b"{}").status_code == 401
