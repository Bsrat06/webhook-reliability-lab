"""Durable delivery processing.

Flow per job: claim (short tx, lease) -> prepare (short tx) -> HTTP with bounded timeouts (no tx open)
-> persist attempt + next state (short tx, lease-checked). At-least-once: a crash between the HTTP call
and the final commit causes the attempt to be repeated after the lease expires.
"""

import json
import logging
import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import decrypt_secret, sign_payload
from app.domain.retry import RetryPolicySpec, delay_after_failure, is_retryable
from app.models import (
    TERMINAL_DELIVERY_STATUSES,
    DeadLetter,
    Delivery,
    DeliveryAttempt,
    Endpoint,
    Event,
    Job,
)
from app.services.events import enqueue_delivery_job, record_transition

log = logging.getLogger("worker.delivery")
MAX_JOB_CLAIMS = 5

# Claims are serialized with a transaction-scoped advisory lock so per-group running counts are exact;
# the claim transaction is tiny, so this costs little. SKIP LOCKED still guards against row contention.
CLAIM_LOCK_SQL = text("SELECT pg_advisory_xact_lock(727201)")
CLAIM_SQL = text(
    """
    WITH running AS (
        SELECT concurrency_key, count(*) AS n FROM jobs
        WHERE status = 'running' AND lease_expires_at >= now() AND concurrency_key IS NOT NULL
        GROUP BY concurrency_key
    ), due AS (
        SELECT id, scheduled_at, concurrency_key, concurrency_limit FROM jobs
        WHERE (status = 'queued' AND scheduled_at <= now())
           OR (status = 'running' AND lease_expires_at < now())
        ORDER BY scheduled_at
        LIMIT 5000
    ), ranked AS (
        SELECT d.id, d.scheduled_at, d.concurrency_key, d.concurrency_limit,
               row_number() OVER (PARTITION BY d.concurrency_key ORDER BY d.scheduled_at) + coalesce(r.n, 0) AS slot
        FROM due d LEFT JOIN running r ON r.concurrency_key = d.concurrency_key
    )
    UPDATE jobs SET status = 'running', lease_owner = :worker,
        lease_expires_at = now() + make_interval(secs => :lease), claim_count = claim_count + 1, updated_at = now()
    WHERE id IN (
        SELECT j.id FROM jobs j JOIN ranked k ON k.id = j.id
        WHERE k.concurrency_key IS NULL OR k.concurrency_limit IS NULL OR k.slot <= k.concurrency_limit
        ORDER BY k.scheduled_at
        LIMIT :n
        FOR UPDATE OF j SKIP LOCKED
    )
    RETURNING id, job_type, payload, claim_count
    """
)


@dataclass
class ClaimedJob:
    id: uuid.UUID
    job_type: str
    payload: dict
    claim_count: int


def claim_jobs(db: Session, worker_id: str, n: int) -> list[ClaimedJob]:
    db.execute(CLAIM_LOCK_SQL)
    rows = db.execute(CLAIM_SQL, {"worker": worker_id, "lease": get_settings().worker_lease_seconds, "n": n}).all()
    db.commit()
    return [ClaimedJob(r.id, r.job_type, r.payload, r.claim_count) for r in rows]


def _held_job(db: Session, job_id: uuid.UUID, worker_id: str) -> Job | None:
    return db.scalar(
        select(Job).where(Job.id == job_id, Job.lease_owner == worker_id, Job.status == "running").with_for_update()
    )


def _finish_job(job: Job, status: str = "succeeded", error: str | None = None) -> None:
    job.status = status
    job.last_error = error
    job.completed_at = func.now()
    job.lease_owner = None
    job.lease_expires_at = None


@dataclass
class Prepared:
    delivery_id: uuid.UUID
    url: str
    body: bytes
    headers: dict[str, str]
    attempt_number: int
    timeout_ms: int


def _prepare(db: Session, job: ClaimedJob, worker_id: str) -> Prepared | None:
    settings = get_settings()
    held = _held_job(db, job.id, worker_id)
    if held is None:
        db.rollback()
        return None
    d = db.scalar(select(Delivery).where(Delivery.id == uuid.UUID(job.payload["delivery_id"])).with_for_update())
    if d is None or d.status in TERMINAL_DELIVERY_STATUSES:
        _finish_job(held)  # Idempotent: work already finished (or delivery gone).
        db.commit()
        return None
    if job.claim_count > MAX_JOB_CLAIMS:
        record_transition(db, d, "dead_lettered", "worker repeatedly failed to complete this job")
        d.completed_at = func.now()
        d.last_error_category = "worker_error"
        db.add(DeadLetter(workspace_id=d.workspace_id, delivery_id=d.id, reason="Worker failed to complete delivery job"))
        _finish_job(held, "failed", "exceeded max claims")
        db.commit()
        _after_terminal(db, d.simulation_run_id)
        return None
    ep = db.get(Endpoint, d.endpoint_id)
    ev = db.get(Event, d.event_id)
    if not ep.enabled:
        record_transition(db, d, "cancelled", "endpoint disabled")
        d.completed_at = func.now()
        _finish_job(held)
        db.commit()
        _after_terminal(db, d.simulation_run_id)
        return None
    if d.status != "processing":
        record_transition(db, d, "processing", f"claimed by {worker_id}")
    attempt_number = d.attempt_count + 1
    body = json.dumps(
        {"id": str(ev.id), "type": ev.event_type, "created_at": ev.created_at.isoformat(), "data": ev.payload},
        separators=(",", ":"),
    ).encode()
    headers = {
        "content-type": "application/json",
        "user-agent": "webhook-lab-worker/0.1",
        "x-webhook-id": str(d.id),
        "x-webhook-event-id": str(ev.id),
        "x-webhook-event-type": ev.event_type,
        "x-webhook-attempt": str(attempt_number),
        "x-webhook-correlation-id": ev.correlation_id,
        "x-webhook-signature": sign_payload(decrypt_secret(ep.secret_ciphertext), body),
    }
    if ev.idempotency_key:
        headers["idempotency-key"] = ev.idempotency_key
    if d.simulation_run_id and ep.endpoint_type == "builtin":
        # The built-in receiver applies the run's stored behavior and seeded decision key.
        headers["x-simulation-run-id"] = str(d.simulation_run_id)
        headers["x-webhook-decision-key"] = d.simulation_key or str(ev.id)
    url = ep.destination_url if ep.endpoint_type == "external" else f"{settings.receiver_base_url}/receiver/{ep.id}"
    timeout_ms = RetryPolicySpec.model_validate(d.retry_policy).timeout_ms
    db.commit()
    return Prepared(d.id, url, body, headers, attempt_number, timeout_ms)


@dataclass
class HttpResult:
    started: datetime
    completed: datetime
    duration_ms: int
    status: int | None = None
    headers: dict | None = None
    excerpt: str | None = None
    error_category: str | None = None
    error_message: str | None = None


SAFE_RESPONSE_HEADERS = {
    "content-type",
    "content-length",
    "retry-after",
    "date",
    "x-receiver-decision",
    "x-receiver-result",
}


def _send(client: httpx.Client, p: Prepared) -> HttpResult:
    settings = get_settings()
    started = datetime.now(UTC)
    t0 = time.monotonic()
    result = HttpResult(started=started, completed=started, duration_ms=0)
    t = p.timeout_ms / 1000
    timeout = httpx.Timeout(t, connect=min(5.0, t))
    try:
        with client.stream("POST", p.url, content=p.body, headers=p.headers, timeout=timeout) as resp:
            buf = b""
            for chunk in resp.iter_bytes():
                buf += chunk
                if len(buf) >= settings.response_excerpt_bytes:
                    break  # Bounded response size: stop reading.
            result.status = resp.status_code
            result.headers = {k: v[:200] for k, v in resp.headers.items() if k.lower() in SAFE_RESPONSE_HEADERS}
            result.excerpt = buf[: settings.response_excerpt_bytes].decode("utf-8", "replace")
    except httpx.TimeoutException:
        result.error_category, result.error_message = "timeout", f"No response within {p.timeout_ms} ms"
    except httpx.ConnectError:
        result.error_category, result.error_message = "connection_error", "Could not connect to destination"
    except (httpx.RemoteProtocolError, httpx.ReadError):
        result.error_category = "connection_reset"
        result.error_message = "Connection closed before the response completed"
    except httpx.HTTPError as exc:
        result.error_category, result.error_message = "network_error", type(exc).__name__
    result.completed = datetime.now(UTC)
    result.duration_ms = int((time.monotonic() - t0) * 1000)
    return result


def _category_for_status(status: int) -> str | None:
    if 200 <= status < 300:
        return None
    if status == 429:
        return "rate_limited"
    return "http_5xx" if status >= 500 else "http_4xx"


def _retry_after(r: HttpResult) -> float | None:
    if r.status not in (429, 503) or not r.headers:
        return None
    try:
        return max(0.0, float(r.headers.get("retry-after", "")))
    except ValueError:
        return None


def _jitter_rng(d: Delivery) -> random.Random:
    # Simulation retries use seeded jitter so the retry schedule is reproducible too.
    return random.Random(f"{d.simulation_key}:{d.attempt_count}") if d.simulation_key else random.Random()


def _after_terminal(db: Session, run_id: uuid.UUID | None) -> None:
    if run_id is not None:
        from app.services.simulations import finalize_if_done

        finalize_if_done(db, run_id)


def _record(db: Session, job: ClaimedJob, worker_id: str, p: Prepared, r: HttpResult) -> str:
    d = db.scalar(select(Delivery).where(Delivery.id == p.delivery_id).with_for_update())
    if d is None:
        db.rollback()
        return "gone"
    policy = RetryPolicySpec.model_validate(d.retry_policy)
    category = r.error_category or _category_for_status(r.status)
    if category is None:
        outcome = "success"
    elif is_retryable(policy, r.status, r.error_category):
        outcome = "retryable_failure"
    else:
        outcome = "permanent_failure"
    # Attempts are always recorded, even if our lease was lost: a duplicate attempt is real and must be visible.
    d.attempt_count += 1
    redacted = {k: (v.split(",v1=")[0] + ",v1=…" if k == "x-webhook-signature" else v) for k, v in p.headers.items()}
    db.add(
        DeliveryAttempt(
            workspace_id=d.workspace_id,
            delivery_id=d.id,
            attempt_number=d.attempt_count,
            request_started_at=r.started,
            request_completed_at=r.completed,
            duration_ms=r.duration_ms,
            request_headers=redacted,
            response_status=r.status,
            response_headers=r.headers or {},
            response_body_excerpt=r.excerpt,
            error_category=category,
            error_message=r.error_message or (f"HTTP {r.status}" if category else None),
            outcome=outcome,
            worker_id=worker_id,
        )
    )
    held = _held_job(db, job.id, worker_id)
    if held is None:
        db.commit()
        return "lease_lost"
    if d.status in TERMINAL_DELIVERY_STATUSES:
        # Cancelled (or finished elsewhere) while in flight: keep the attempt, schedule nothing.
        _finish_job(held)
        db.commit()
        return "superseded"
    d.last_error_category = category
    now = func.now()
    if outcome == "success":
        record_transition(db, d, "succeeded", f"attempt {d.attempt_count}: HTTP {r.status}")
        d.completed_at, d.next_attempt_at = now, None
    elif outcome == "retryable_failure" and d.attempt_count < policy.max_attempts:
        delay = delay_after_failure(policy, d.attempt_count, _retry_after(r), _jitter_rng(d))
        d.next_attempt_at = func.now() + timedelta(milliseconds=delay)
        record_transition(db, d, "retry_scheduled", f"attempt {d.attempt_count} failed ({category}); retry in {delay} ms")
        enqueue_delivery_job(db, d, delay)
    else:
        reason = (
            f"retries exhausted after {d.attempt_count} attempts ({category})"
            if outcome == "retryable_failure"
            else f"non-retryable failure ({category})"
        )
        record_transition(db, d, "dead_lettered", reason)
        d.completed_at, d.next_attempt_at = now, None
        db.add(DeadLetter(workspace_id=d.workspace_id, delivery_id=d.id, reason=reason))
    _finish_job(held)
    db.commit()
    if d.status in TERMINAL_DELIVERY_STATUSES:
        _after_terminal(db, d.simulation_run_id)
    return outcome


def make_client() -> httpx.Client:
    s = get_settings()
    timeout = httpx.Timeout(s.delivery_timeout_seconds, connect=min(5.0, s.delivery_timeout_seconds))
    return httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False)


def _system_handlers() -> dict:
    from app.services.retention import run_cleanup
    from app.services.simulations import enforce_deadline

    return {"cleanup": run_cleanup, "simulation_deadline": enforce_deadline}


def _process_system_job(session_factory, job: ClaimedJob, worker_id: str) -> str:
    handler = _system_handlers().get(job.job_type)
    with session_factory() as db:
        held = _held_job(db, job.id, worker_id)
        if held is None:
            db.rollback()
            return "lease_lost"
        if handler is None:
            _finish_job(held, "failed", f"unknown job type {job.job_type}")
            db.commit()
            return "unknown"
        db.commit()
    # Handlers run their own short transactions and are idempotent; the lease prevents parallel runs.
    with session_factory() as db:
        result = handler(db, job.payload)
    with session_factory() as db:
        held = _held_job(db, job.id, worker_id)
        if held:
            _finish_job(held)
        db.commit()
    return result


def process_job(session_factory, client: httpx.Client, job: ClaimedJob, worker_id: str) -> str:
    if job.job_type != "deliver":
        return _process_system_job(session_factory, job, worker_id)
    with session_factory() as db:
        prepared = _prepare(db, job, worker_id)
    if prepared is None:
        return "skipped"
    result = _send(client, prepared)
    with session_factory() as db:
        return _record(db, job, worker_id, prepared, result)
