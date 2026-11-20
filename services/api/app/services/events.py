import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ServiceError, not_found, quota_exceeded
from app.models import (
    TERMINAL_DELIVERY_STATUSES,
    DeadLetter,
    Delivery,
    DeliveryAttempt,
    DeliveryTransition,
    Endpoint,
    Event,
    Job,
    Workspace,
)


def enqueue_delivery_job(
    db: Session, delivery: Delivery, delay_ms: int = 0, concurrency_limit: int | None = None
) -> None:
    """Schedule on the database clock so API, worker and DB never disagree about due time."""
    limit = get_settings().quota_workspace_concurrency
    db.add(
        Job(
            workspace_id=delivery.workspace_id,
            job_type="deliver",
            payload={"delivery_id": str(delivery.id)},
            # One job per (delivery, attempt) so a retry can never be enqueued twice.
            dedupe_key=f"deliver:{delivery.id}:{delivery.attempt_count + 1}",
            scheduled_at=func.now() + timedelta(milliseconds=delay_ms),
            # Per-workspace in-flight limit, so one guest cannot occupy every worker slot.
            concurrency_key=f"ws:{delivery.workspace_id}",
            concurrency_limit=min(limit, concurrency_limit or limit),
        )
    )


def new_delivery(
    db: Session, *, event: Event, endpoint: Endpoint, retry_policy: dict, reason: str = "created", **extra
) -> Delivery:
    """Persist a pending delivery with its first transition. The caller enqueues and commits."""
    d = Delivery(
        workspace_id=event.workspace_id,
        event_id=event.id,
        endpoint_id=endpoint.id,
        status="pending",
        retry_policy=retry_policy,
        **extra,
    )
    db.add(d)
    db.flush()
    db.add(
        DeliveryTransition(
            workspace_id=d.workspace_id, delivery_id=d.id, from_status=None, to_status="pending", reason=reason
        )
    )
    return d


def record_transition(db: Session, d: Delivery, to_status: str, reason: str = "") -> None:
    db.add(
        DeliveryTransition(
            workspace_id=d.workspace_id, delivery_id=d.id, from_status=d.status, to_status=to_status, reason=reason
        )
    )
    d.status = to_status


def create_event(
    db: Session,
    ws: Workspace,
    *,
    event_type: str,
    payload: dict,
    endpoint_ids: list[uuid.UUID],
    idempotency_key: str | None = None,
    metadata: dict | None = None,
    source: str = "manual",
) -> Event:
    settings = get_settings()
    size = len(json.dumps(payload, separators=(",", ":")).encode())
    if size > settings.quota_payload_bytes:
        raise ServiceError(
            413, "payload_too_large", f"Payload is {size} bytes; the limit is {settings.quota_payload_bytes} bytes."
        )
    recent = db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.workspace_id == ws.id, Event.created_at >= func.now() - timedelta(hours=1))
    )
    if recent >= settings.quota_events_per_hour:
        raise quota_exceeded("Hourly event", settings.quota_events_per_hour, "Wait a while before sending more events.")

    unique_ids = list(dict.fromkeys(endpoint_ids))
    endpoints = list(db.scalars(select(Endpoint).where(Endpoint.workspace_id == ws.id, Endpoint.id.in_(unique_ids))))
    if not unique_ids or len(endpoints) != len(unique_ids):
        raise ServiceError(422, "invalid_endpoints", "Select at least one endpoint from this workspace.")
    disabled = [e.name for e in endpoints if not e.enabled]
    if disabled:
        raise ServiceError(422, "endpoint_disabled", f"Endpoint disabled: {', '.join(disabled)}.")

    event = Event(
        workspace_id=ws.id,
        event_type=event_type,
        payload=payload,
        metadata_=metadata or {},
        idempotency_key=idempotency_key,
        correlation_id=uuid.uuid4().hex,
        source=source,
    )
    db.add(event)
    db.flush()
    # Event, deliveries and their initial jobs commit atomically: no delivery without a job.
    for ep in endpoints:
        enqueue_delivery_job(db, new_delivery(db, event=event, endpoint=ep, retry_policy=ep.retry_policy))
    db.commit()
    return event


def aggregate_status(deliveries: list[Delivery]) -> str:
    """Status of an event, judged by the latest delivery in each replay chain (replayed originals are superseded)."""
    replayed = {d.replay_of_delivery_id for d in deliveries if d.replay_of_delivery_id}
    statuses = [d.status for d in deliveries if d.id not in replayed]
    if not statuses:
        return "no_deliveries"
    if any(s not in TERMINAL_DELIVERY_STATUSES for s in statuses):
        return "in_progress"
    if all(s == "succeeded" for s in statuses):
        return "delivered"
    if any(s == "succeeded" for s in statuses):
        return "partially_failed"
    return "failed"


def list_events(
    db: Session,
    ws: Workspace,
    *,
    limit: int,
    cursor: tuple[datetime, uuid.UUID] | None = None,
    event_type: str | None = None,
    q: str | None = None,
    source: str | None = None,
    simulation_run_id: uuid.UUID | None = None,
    delivery_status: str | None = None,
) -> list[tuple[Event, list[Delivery]]]:
    stmt = select(Event).where(Event.workspace_id == ws.id)
    if cursor:
        # Keyset pagination on (created_at, id): bulk-created events share a timestamp.
        stmt = stmt.where(tuple_(Event.created_at, Event.id) < tuple_(*cursor))
    if source:
        stmt = stmt.where(Event.source == source)
    if simulation_run_id:
        stmt = stmt.where(Event.simulation_run_id == simulation_run_id)
    if delivery_status:
        match = select(Delivery.id).where(Delivery.event_id == Event.id)
        if delivery_status == "retried":
            match = match.where(Delivery.attempt_count > 1)
        else:
            match = match.where(Delivery.status == delivery_status)
        stmt = stmt.where(match.exists())
    if event_type:
        stmt = stmt.where(Event.event_type == event_type)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(
            Event.event_type.ilike(like) | Event.correlation_id.ilike(like) | Event.idempotency_key.ilike(like)
        )
    events = list(db.scalars(stmt.order_by(Event.created_at.desc(), Event.id.desc()).limit(limit)))
    by_event: dict[uuid.UUID, list[Delivery]] = {e.id: [] for e in events}
    if events:
        for d in db.scalars(select(Delivery).where(Delivery.event_id.in_(by_event.keys()))):
            by_event[d.event_id].append(d)
    return [(e, by_event[e.id]) for e in events]


def get_event_detail(db: Session, ws: Workspace, event_id: uuid.UUID) -> dict:
    event = db.scalar(select(Event).where(Event.id == event_id, Event.workspace_id == ws.id))
    if event is None:
        raise not_found("Event")
    deliveries = list(
        db.scalars(
            select(Delivery)
            .where(Delivery.event_id == event.id, Delivery.workspace_id == ws.id)
            .order_by(Delivery.created_at)
        )
    )
    ids = [d.id for d in deliveries]
    attempts = (
        list(
            db.scalars(
                select(DeliveryAttempt)
                .where(DeliveryAttempt.delivery_id.in_(ids))
                .order_by(DeliveryAttempt.attempt_number)
            )
        )
        if ids
        else []
    )
    transitions = (
        list(
            db.scalars(
                select(DeliveryTransition)
                .where(DeliveryTransition.delivery_id.in_(ids))
                .order_by(DeliveryTransition.id)
            )
        )
        if ids
        else []
    )
    dead = list(db.scalars(select(DeadLetter).where(DeadLetter.delivery_id.in_(ids)))) if ids else []
    endpoints = (
        {e.id: e for e in db.scalars(select(Endpoint).where(Endpoint.id.in_({d.endpoint_id for d in deliveries})))}
        if ids
        else {}
    )
    return {
        "event": event,
        "deliveries": deliveries,
        "attempts": attempts,
        "transitions": transitions,
        "dead_letters": dead,
        "endpoints": endpoints,
    }
