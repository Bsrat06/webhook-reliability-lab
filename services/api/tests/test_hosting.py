"""Single-service hosting: the worker runs inside the API process when RUN_WORKER_IN_API is set."""

import threading
import time

from fastapi.testclient import TestClient

from app import main


def test_worker_runs_inside_api_process(monkeypatch, live_server):
    monkeypatch.setattr(main.settings, "run_worker_in_api", True)
    with TestClient(main.app, headers={"x-requested-with": "fetch"}) as c:
        c.post("/api/v1/guest/session").raise_for_status()
        (ep,) = c.get("/api/v1/endpoints").json()
        r = c.post("/api/v1/events", json={"event_type": "order.created", "payload": {}, "endpoint_ids": [ep["id"]]})
        ev = r.json()
        deadline = time.monotonic() + 15
        while c.get(f"/api/v1/events/{ev['id']}").json()["status"] != "delivered":
            assert time.monotonic() < deadline, "in-process worker did not deliver the event"
            time.sleep(0.1)
    # Leaving the client shut the worker down: nothing claims new work afterwards.
    assert not [t for t in threading.enumerate() if t.name == "delivery-worker"]
