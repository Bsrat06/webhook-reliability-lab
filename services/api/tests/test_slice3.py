"""Slice 3: simulation lab — presets, seeded reproducibility, progress, cancellation, quotas, results."""

import threading

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.main import app
from app.services.simulations import receiver_behavior_for_run
from tests.conftest import run_worker_until

FAST_POLICY = {"max_attempts": 3, "initial_delay_ms": 100, "max_delay_ms": 200, "jitter": "full", "timeout_ms": 2000}


def _ep(c) -> str:
    return c.get("/api/v1/endpoints").json()[0]["id"]


def _preset(c, key: str) -> dict:
    return next(p for p in c.get("/api/v1/simulations/presets").json() if p["key"] == key)


def _start(c, config: dict, preset: str | None = None, **extra) -> dict:
    r = c.post("/api/v1/simulations", json={"preset": preset, "config": config, **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _run(c, run_id) -> dict:
    return c.get(f"/api/v1/simulations/{run_id}").json()


def _wait(c, run_id, timeout=60.0):
    run_worker_until(lambda: _run(c, run_id)["status"] != "running", timeout=timeout)
    return _run(c, run_id)


def _decisions(run_id: str) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT d.simulation_key, a.attempt_number, a.response_status, a.error_category "
                "FROM delivery_attempts a JOIN deliveries d ON d.id = a.delivery_id WHERE d.simulation_run_id = :id"
            ),
            {"id": run_id},
        ).all()
    # Drop the seed prefix so runs with different seeds are compared position by position.
    return {(k.split(":", 1)[1], n): (s, c) for k, n, s, c in rows}


def test_presets_map_to_distinct_executable_configs(guest):
    presets = guest.get("/api/v1/simulations/presets").json()
    assert len(presets) == 8
    configs = {
        p["key"]: (p["config"]["receiver"], p["config"]["retry_policy"], p["config"]["duplicate_send_rate"])
        for p in presets
    }
    assert len({repr(v) for v in configs.values()}) == 8
    assert configs["rate_limited"][0]["failure_mix"]["rate_limited"] > 0
    assert configs["slow_timeouts"][1]["timeout_ms"] == 1000
    assert configs["duplicate_challenge"][2] > 0


def test_healthy_run_completes_with_persisted_results(guest, live_server):
    cfg = {**_preset(guest, "healthy")["config"], "endpoint_ids": [_ep(guest)], "event_count": 20, "concurrency": 4}
    run = _start(guest, cfg, preset="healthy")
    assert run["status"] == "running" and run["delivery_count"] == 20
    done = _wait(guest, run["id"])
    s = done["summary"]
    assert done["status"] == "completed" and s["progress"] == 1
    assert s["by_status"]["succeeded"] == 20 and s["success_rate"] == 1.0 and s["attempts"] == 20 and s["retries"] == 0
    assert s["latency_ms"]["n"] == 20 and s["latency_ms"]["p50"] is not None
    page = guest.get(f"/api/v1/simulations/{run['id']}/events?limit=100").json()
    assert len(page["items"]) == 20 and all(e["source"] == "simulation" for e in page["items"])
    # Simulation events are excluded from the manual event filter.
    assert guest.get("/api/v1/events?source=manual").json()["items"] == []
    stored = done["summary"]
    with SessionLocal() as db:
        snapshot = db.execute(
            text("SELECT summary FROM simulation_runs WHERE id = :id"), {"id": run["id"]}
        ).scalar_one()
    assert snapshot["attempts"] == stored["attempts"]


def test_same_seed_reproduces_failure_decisions(guest, live_server):
    cfg = {
        "endpoint_ids": [_ep(guest)],
        "event_count": 30,
        "concurrency": 4,
        "seed": 99,
        "retry_policy": FAST_POLICY,
        "receiver": {
            "mode": "random",
            "failure_rate": 0.5,
            "failure_mix": {"server_error": 1, "rate_limited": 1, "connection_drop": 1, "timeout": 0},
            "retry_after_s": 0,
            "latency_ms_min": 5,
            "latency_ms_max": 30,
        },
    }
    first = _wait(guest, _start(guest, cfg)["id"])
    second = _wait(guest, _start(guest, cfg)["id"])
    third = _wait(guest, _start(guest, {**cfg, "seed": 100})["id"])
    a, b, c = _decisions(first["id"]), _decisions(second["id"]), _decisions(third["id"])
    assert a and a == b
    assert a != c
    for k in ("by_status", "attempts", "retries", "failure_categories"):
        assert first["summary"][k] == second["summary"][k]
    assert {cat for _, cat in a.values()} >= {None, "http_5xx", "rate_limited", "connection_reset"}


def test_cancel_stops_remaining_deliveries(guest, live_server):
    cfg = {"endpoint_ids": [_ep(guest)], "event_count": 50, "emit_over_ms": 60_000, "retry_policy": FAST_POLICY}
    run = _start(guest, cfg)
    r = guest.post(f"/api/v1/simulations/{run['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    s = r.json()["summary"]
    assert s["by_status"]["cancelled"] + s["by_status"]["succeeded"] == 50 and s["success_rate"] in (None, 1.0)
    with engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM jobs WHERE status = 'queued' AND job_type = 'deliver'")
            ).scalar_one()
            == 0
        )
    assert guest.post(f"/api/v1/simulations/{run['id']}/cancel").status_code == 409
    assert "simulation.cancelled" in [a["action"] for a in guest.get("/api/v1/workspaces/current/audit").json()]


def test_runtime_limit_is_enforced_by_durable_deadline_job(guest, live_server):
    cfg = {"endpoint_ids": [_ep(guest)], "event_count": 20, "emit_over_ms": 60_000, "max_runtime_s": 10}
    run = _start(guest, cfg)
    with engine.begin() as conn:
        conn.execute(text("UPDATE jobs SET scheduled_at = now() WHERE job_type = 'simulation_deadline'"))
    done = _wait(guest, run["id"])
    assert done["status"] == "timed_out" and "10s runtime limit" in done["end_reason"]
    assert done["summary"]["by_status"]["cancelled"] > 0


def test_simulation_quotas(guest, live_server):
    ep = _ep(guest)
    assert (
        guest.post("/api/v1/simulations", json={"config": {"endpoint_ids": [ep], "event_count": 1001}}).status_code
        == 422
    )
    r = guest.post("/api/v1/simulations", json={"config": {"endpoint_ids": [ep], "concurrency": 8}})
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"
    _start(guest, {"endpoint_ids": [ep], "event_count": 5, "emit_over_ms": 60_000})
    r = guest.post("/api/v1/simulations", json={"config": {"endpoint_ids": [ep], "event_count": 5}})
    assert r.status_code == 409 and r.json()["error"]["code"] == "simulation_active"
    settings = get_settings()
    original = settings.quota_simulation_events_per_hour
    settings.quota_simulation_events_per_hour = 8
    try:
        run_id = guest.get("/api/v1/simulations").json()[0]["id"]
        guest.post(f"/api/v1/simulations/{run_id}/cancel")
        r = guest.post("/api/v1/simulations", json={"config": {"endpoint_ids": [ep], "event_count": 5}})
        assert r.status_code == 429 and "per hour" in r.json()["error"]["message"]
    finally:
        settings.quota_simulation_events_per_hour = original


def test_simulation_isolation(guest, live_server):
    run = _start(guest, {"endpoint_ids": [_ep(guest)], "event_count": 5, "emit_over_ms": 60_000})
    with TestClient(app, headers={"x-requested-with": "fetch"}) as other:
        other.post("/api/v1/guest/session")
        other_ws = other.get("/api/v1/session").json()["workspace"]["id"]
        for path in ("", "/summary", "/events", "/stream"):
            assert other.get(f"/api/v1/simulations/{run['id']}{path}").status_code == 404, path
        assert other.post(f"/api/v1/simulations/{run['id']}/cancel").status_code == 404
        assert other.post("/api/v1/simulations/compare", json={"run_ids": [run["id"], run["id"]]}).status_code == 404
        assert other.get("/api/v1/simulations").json() == []
        # A forged run header cannot pull another workspace's simulation behavior into this receiver.
        with SessionLocal() as db:
            assert receiver_behavior_for_run(db, other_ws, run["id"]) is None
        r = other.post("/api/v1/simulations", json={"config": {"endpoint_ids": [_ep(guest)]}})
        assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_endpoints"


def test_compare_runs_shows_outcome_and_config_differences(guest, live_server):
    ep = _ep(guest)
    base = {
        "endpoint_ids": [ep],
        "event_count": 20,
        "concurrency": 4,
        "seed": 5,
        "retry_policy": FAST_POLICY,
        "receiver": {"mode": "random", "failure_rate": 0.5, "latency_ms_min": 5, "latency_ms_max": 20},
    }
    a = _wait(guest, _start(guest, base)["id"])
    b = _wait(guest, _start(guest, {**base, "retry_policy": {**FAST_POLICY, "max_attempts": 6}})["id"])
    r = guest.post("/api/v1/simulations/compare", json={"run_ids": [a["id"], b["id"]]})
    assert r.status_code == 200
    body = r.json()
    assert [x["id"] for x in body["runs"]] == [a["id"], b["id"]]
    diff = {d["key"]: d["values"] for d in body["config_differences"]}
    assert diff == {"retry_policy.max_attempts": [3, 6]}
    sa, sb = (x["summary"] for x in body["runs"])
    # Same seed and failure decisions, more attempts allowed: never fewer successes.
    assert sb["by_status"]["succeeded"] >= sa["by_status"]["succeeded"]


def test_live_progress_stream(guest, live_server):
    run = _start(guest, {"endpoint_ids": [_ep(guest)], "event_count": 15, "concurrency": 2, "emit_over_ms": 1500})
    received: list[str] = []
    stop = threading.Event()

    def worker():
        try:
            run_worker_until(lambda: stop.is_set(), timeout=60)
        except AssertionError:
            pass

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    try:
        with guest.stream("GET", f"/api/v1/simulations/{run['id']}/stream?interval=fast") as resp:
            assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/event-stream")
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    received.append(line.split(":", 1)[1].strip())
                if received and received[-1] == "done":
                    break
    finally:
        stop.set()
        t.join(timeout=15)
    assert received.count("progress") >= 2 and received[-1] == "done"
    assert _run(guest, run["id"])["status"] == "completed"


def test_duplicate_challenge_suppresses_duplicates(guest, live_server):
    cfg = {
        **_preset(guest, "duplicate_challenge")["config"],
        "endpoint_ids": [_ep(guest)],
        "event_count": 40,
        "concurrency": 4,
        "seed": 3,
    }
    run = _start(guest, cfg, preset="duplicate_challenge")
    assert run["event_count"] > 40  # producer duplicates were added deterministically
    done = _wait(guest, run["id"])
    s = done["summary"]
    assert s["success_rate"] == 1.0
    assert s["duplicates_suppressed"] > 0
    assert s["failure_categories"].get("connection_reset", 0) > 0
