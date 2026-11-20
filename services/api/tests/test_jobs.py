"""Durable job claiming: no double claims under concurrency; expired leases are recovered."""

import threading

from sqlalchemy import text

from app.db.session import SessionLocal, engine
from app.workers.delivery import claim_jobs
from tests.conftest import run_worker_until


def _send_many(c, n):
    ep = c.get("/api/v1/endpoints").json()[0]
    return [
        c.post("/api/v1/events", json={"event_type": "t", "payload": {"i": i}, "endpoint_ids": [ep["id"]]}).json()["id"]
        for i in range(n)
    ]


def test_concurrent_claims_never_overlap(guest):
    _send_many(guest, 30)
    # Lift the per-workspace limit: this test is about overlap safety, not concurrency caps.
    with engine.begin() as conn:
        conn.execute(text("UPDATE jobs SET concurrency_key = NULL"))
    claimed: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(5)

    def claimer(i):
        barrier.wait()
        # SKIP LOCKED may return short batches under contention, so drain until empty.
        while True:
            with SessionLocal() as db:
                jobs = claim_jobs(db, f"w{i}", 3)
            if not jobs:
                return
            with lock:
                claimed.extend(j.id for j in jobs)

    threads = [threading.Thread(target=claimer, args=(i,)) for i in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(claimed) == 30 and len(set(claimed)) == 30


def test_crashed_worker_lease_is_recovered(guest, live_server):
    (event_id,) = _send_many(guest, 1)
    with SessionLocal() as db:
        (job,) = claim_jobs(db, "crashed-worker", 10)
    # Simulate a crash: the lease holder never finishes; force its lease to be expired.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE jobs SET lease_expires_at = now() - interval '1 second' WHERE id = :id"), {"id": job.id}
        )
    run_worker_until(lambda: guest.get(f"/api/v1/events/{event_id}").json()["status"] == "delivered")
    with engine.connect() as conn:
        claims = conn.execute(text("SELECT claim_count FROM jobs WHERE id = :id"), {"id": job.id}).scalar_one()
    assert claims == 2


def test_jobs_scheduled_in_future_wait(guest):
    _send_many(guest, 1)
    with engine.begin() as conn:
        conn.execute(text("UPDATE jobs SET scheduled_at = now() + interval '1 hour'"))
    with SessionLocal() as db:
        assert claim_jobs(db, "w", 10) == []
