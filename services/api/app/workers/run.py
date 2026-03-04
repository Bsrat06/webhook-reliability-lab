"""Worker process entrypoint: `python -m app.workers.run`."""

import logging
import os
import signal
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models import WorkerHeartbeat
from app.services.retention import schedule_cleanup
from app.workers.delivery import claim_jobs, make_client, process_job

log = logging.getLogger("worker")


def heartbeat(worker_id: str) -> None:
    with SessionLocal() as db:
        stmt = insert(WorkerHeartbeat).values(worker_id=worker_id)
        db.execute(stmt.on_conflict_do_update(index_elements=["worker_id"], set_={"last_seen_at": func.now()}))
        db.commit()


def run(stop: threading.Event | None = None, concurrency: int | None = None) -> None:
    settings = get_settings()
    stop = stop or threading.Event()
    concurrency = concurrency or int(os.environ.get("WORKER_CONCURRENCY", "8"))
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident() % 10000}"
    client = make_client()
    in_flight: set = set()
    lock = threading.Lock()
    last_beat = 0.0
    last_cleanup = 0.0
    log.info("worker %s started (concurrency=%d)", worker_id, concurrency)
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        while not stop.is_set():
            try:
                if time.monotonic() - last_beat > 5:
                    heartbeat(worker_id)
                    last_beat = time.monotonic()
                if time.monotonic() - last_cleanup > settings.cleanup_interval_seconds:
                    with SessionLocal() as db:
                        schedule_cleanup(db)
                    last_cleanup = time.monotonic()
                with lock:
                    free = concurrency - len(in_flight)
                jobs = []
                if free > 0:
                    with SessionLocal() as db:
                        jobs = claim_jobs(db, worker_id, min(free, settings.worker_batch_size))
                for job in jobs:
                    fut = pool.submit(process_job, SessionLocal, client, job, worker_id)
                    with lock:
                        in_flight.add(fut)

                    def _done(f, _job=job):
                        with lock:
                            in_flight.discard(f)
                        if f.exception():
                            # Lease will expire and the job will be re-claimed (bounded by MAX_JOB_CLAIMS).
                            log.error("job %s failed: %r", _job.id, f.exception())

                    fut.add_done_callback(_done)
                if not jobs:
                    stop.wait(settings.worker_poll_interval_seconds)
            except Exception:
                log.exception("worker loop error")
                stop.wait(2)
        log.info("worker %s draining %d in-flight jobs", worker_id, len(in_flight))
    client.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    run(stop)


if __name__ == "__main__":
    main()
