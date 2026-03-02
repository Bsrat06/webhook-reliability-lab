import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
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


def enqueue_delivery_job(db: Session, delivery: Delivery, delay_ms: int = 0) -> None:
    """Schedule on the database clock so API, worker and DB never disagree about due time."""
    db.add(
        Job(
            workspace_id=delivery.workspace_id,
            job_type="deliver",
            payload={"delivery_id": str(delivery.id)},
            # One job per (delivery, attempt) so a retry can never be enqueued twice.
            dedupe_key=f"deliver:{delivery.id}:{delivery.attempt_count + 1}",
            scheduled_at=func.now() + timedelta(milliseconds=delay_ms),
        )
    )


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
        d = Delivery(workspace_id=ws.id, event_id=event.id, endpoint_id=ep.id, status="pending", retry_policy=ep.retry_policy)
        db.add(d)
        db.flush()
        db.add(DeliveryTransition(workspace_id=ws.id, delivery_id=d.id, from_status=None, to_status="pending", reason="created"))
        enqueue_delivery_job(db, d)
    db.commit()
    return event


def aggregate_status(statuses: list[str]) -> str:
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
    db: Session, ws: Workspace, *, limit: int, before: datetime | None, event_type: str | None, q: str | None
) -> list[tuple[Event, list[Delivery]]]:
    stmt = select(Event).where(Event.workspace_id == ws.id)
    if before:
        stmt = stmt.where(Event.created_at < before)
    if event_type:
        stmt = stmt.where(Event.event_type == event_type)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(Event.event_type.ilike(like) | Event.correlation_id.ilike(like) | Event.idempotency_key.ilike(like))
    events = list(db.scalars(stmt.order_by(Event.created_at.desc()).limit(limit)))
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
        db.scalars(select(Delivery).where(Delivery.event_id == event.id, Delivery.workspace_id == ws.id).order_by(Delivery.created_at))
    )
    ids = [d.id for d in deliveries]
    attempts = list(db.scalars(select(DeliveryAttempt).where(DeliveryAttempt.delivery_id.in_(ids)).order_by(DeliveryAttempt.attempt_number))) if ids else []
    transitions = list(db.scalars(select(DeliveryTransition).where(DeliveryTransition.delivery_id.in_(ids)).order_by(DeliveryTransition.id))) if ids else []
    dead = list(db.scalars(select(DeadLetter).where(DeadLetter.delivery_id.in_(ids)))) if ids else []
    endpoints = {e.id: e for e in db.scalars(select(Endpoint).where(Endpoint.id.in_({d.endpoint_id for d in deliveries})))} if ids else {}
    return {
        "event": event,
        "deliveries": deliveries,
        "attempts": attempts,
        "transitions": transitions,
        "dead_letters": dead,
        "endpoints": endpoints,
    }
