"""Replay, cancellation and dead-letter management. History is never rewritten: replays are new deliveries."""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ServiceError, not_found, quota_exceeded
from app.domain.retry import PRESETS, RetryPolicySpec
from app.models import (
    TERMINAL_DELIVERY_STATUSES,
    DeadLetter,
    Delivery,
    DeliveryAttempt,
    Endpoint,
    Event,
    Job,
    Workspace,
)
from app.services.audit import audit
from app.services.events import enqueue_delivery_job, new_delivery, record_transition


def get_delivery(db: Session, ws: Workspace, delivery_id: uuid.UUID, lock: bool = False) -> Delivery:
    stmt = select(Delivery).where(Delivery.id == delivery_id, Delivery.workspace_id == ws.id)
    d = db.scalar(stmt.with_for_update() if lock else stmt)
    if d is None:
        raise not_found("Delivery")
    return d


def resolve_policy(original: Delivery, endpoint: Endpoint, choice: str, custom: RetryPolicySpec | None) -> dict:
    if choice == "original":
        return RetryPolicySpec.model_validate(original.retry_policy).model_dump()
    if choice == "endpoint":
        return RetryPolicySpec.model_validate(endpoint.retry_policy).model_dump()
    if choice == "custom":
        if custom is None:
            raise ServiceError(422, "policy_required", "Provide retry_policy when policy is 'custom'.")
        return custom.model_dump()
    if choice in PRESETS:
        return PRESETS[choice].model_dump()
    raise ServiceError(422, "unknown_policy", f"Unknown policy '{choice}'.")


def _check_replay_quota(db: Session, ws: Workspace, n: int) -> None:
    limit = get_settings().quota_replays_per_hour
    recent = db.scalar(
        select(func.count())
        .select_from(Delivery)
        .where(
            Delivery.workspace_id == ws.id,
            Delivery.replay_of_delivery_id.is_not(None),
            Delivery.created_at >= func.now() - timedelta(hours=1),
        )
    )
    if recent + n > limit:
        raise quota_exceeded("Hourly replay", limit, f"{recent} replays in the last hour. Wait before replaying more.")


def replay_deliveries(
    db: Session,
    ws: Workspace,
    actor: str,
    delivery_ids: list[uuid.UUID],
    *,
    policy: str = "original",
    custom_policy: RetryPolicySpec | None = None,
) -> list[Delivery]:
    """Validate every delivery first, then create all replays atomically (all or nothing)."""
    ids = list(dict.fromkeys(delivery_ids))
    originals = [get_delivery(db, ws, i, lock=True) for i in ids]
    endpoints = {
        e.id: e for e in db.scalars(select(Endpoint).where(Endpoint.id.in_({d.endpoint_id for d in originals})))
    }
    for d in originals:
        if d.status not in TERMINAL_DELIVERY_STATUSES:
            raise ServiceError(
                409, "delivery_active", f"Delivery {str(d.id)[:8]} is still {d.status}; replay it once it finishes."
            )
        ep = endpoints[d.endpoint_id]
        if not ep.enabled:
            raise ServiceError(422, "endpoint_disabled", f"Endpoint '{ep.name}' is disabled. Enable it to replay.")
    _check_replay_quota(db, ws, len(originals))

    replays = []
    for d in originals:
        ep = endpoints[d.endpoint_id]
        event = db.get(Event, d.event_id)
        r = new_delivery(
            db,
            event=event,
            endpoint=ep,
            retry_policy=resolve_policy(d, ep, policy, custom_policy),
            reason=f"replay of delivery {str(d.id)[:8]} ({policy} policy)",
            replay_of_delivery_id=d.id,
        )
        enqueue_delivery_job(db, r)
        dead = db.scalar(select(DeadLetter).where(DeadLetter.delivery_id == d.id, DeadLetter.resolved_at.is_(None)))
        if dead is not None:
            dead.resolved_at = func.now()
            dead.resolution_note = f"Replayed as delivery {r.id}"
        audit(db, ws.id, actor, "delivery.replayed", "delivery", d.id, replay_delivery_id=str(r.id), policy=policy)
        replays.append(r)
    db.commit()
    return replays


def cancel_delivery(
    db: Session, ws: Workspace, actor: str, delivery_id: uuid.UUID, reason: str = "cancelled by user"
) -> Delivery:
    d = get_delivery(db, ws, delivery_id, lock=True)
    if d.status in TERMINAL_DELIVERY_STATUSES:
        raise ServiceError(409, "delivery_finished", f"Delivery is already {d.status}.")
    cancel_locked(db, d, reason)
    audit(db, ws.id, actor, "delivery.cancelled", "delivery", d.id)
    db.commit()
    return d


def cancel_locked(db: Session, d: Delivery, reason: str) -> None:
    """Cancel a locked, non-terminal delivery. An in-flight attempt is still recorded but schedules nothing."""
    record_transition(db, d, "cancelled", reason)
    d.completed_at = func.now()
    d.next_attempt_at = None
    db.execute(
        update(Job)
        .where(Job.status == "queued", Job.job_type == "deliver", Job.payload["delivery_id"].astext == str(d.id))
        .values(status="succeeded", completed_at=func.now(), last_error="delivery cancelled")
    )


# ---- Dead-letter queue -------------------------------------------------------------------------------


def _last_status_subquery():
    return (
        select(DeliveryAttempt.response_status)
        .where(DeliveryAttempt.delivery_id == DeadLetter.delivery_id)
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
        .correlate(DeadLetter)
        .scalar_subquery()
    )


def list_dead_letters(
    db: Session,
    ws: Workspace,
    *,
    state: str,
    endpoint_id: uuid.UUID | None,
    q: str | None,
    limit: int,
    before: datetime | None,
) -> list[dict]:
    stmt = (
        select(DeadLetter, Delivery, Event.event_type, Endpoint.name, _last_status_subquery().label("last_status"))
        .join(Delivery, Delivery.id == DeadLetter.delivery_id)
        .join(Event, Event.id == Delivery.event_id)
        .join(Endpoint, Endpoint.id == Delivery.endpoint_id)
        .where(DeadLetter.workspace_id == ws.id)
    )
    if state == "open":
        stmt = stmt.where(DeadLetter.resolved_at.is_(None))
    elif state == "resolved":
        stmt = stmt.where(DeadLetter.resolved_at.is_not(None))
    if endpoint_id:
        stmt = stmt.where(Delivery.endpoint_id == endpoint_id)
    if before:
        stmt = stmt.where(DeadLetter.created_at < before)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        stmt = stmt.where(Event.event_type.ilike(like) | DeadLetter.reason.ilike(like))
    rows = db.execute(stmt.order_by(DeadLetter.created_at.desc()).limit(limit)).all()
    replays: dict[uuid.UUID, list[Delivery]] = {}
    if rows:
        for r in db.scalars(
            select(Delivery)
            .where(Delivery.replay_of_delivery_id.in_([row.Delivery.id for row in rows]))
            .order_by(Delivery.created_at)
        ):
            replays.setdefault(r.replay_of_delivery_id, []).append(r)
    return [
        {
            "dead_letter": row.DeadLetter,
            "delivery": row.Delivery,
            "event_type": row.event_type,
            "endpoint_name": row.name,
            "last_status": row.last_status,
            "replays": replays.get(row.Delivery.id, []),
        }
        for row in rows
    ]


def get_dead_letter(db: Session, ws: Workspace, record_id: uuid.UUID) -> DeadLetter:
    dl = db.scalar(select(DeadLetter).where(DeadLetter.id == record_id, DeadLetter.workspace_id == ws.id))
    if dl is None:
        raise not_found("Dead-letter record")
    return dl


def dead_letter_detail(db: Session, ws: Workspace, record_id: uuid.UUID) -> dict:
    item = _single(db, get_dead_letter(db, ws, record_id))
    d = item["delivery"]
    attempts = list(
        db.scalars(
            select(DeliveryAttempt).where(DeliveryAttempt.delivery_id == d.id).order_by(DeliveryAttempt.attempt_number)
        )
    )
    return {**item, "attempts": attempts, "payload": db.get(Event, d.event_id).payload}


def _single(db: Session, dl: DeadLetter) -> dict:
    d = db.get(Delivery, dl.delivery_id)
    last = db.scalar(
        select(DeliveryAttempt.response_status)
        .where(DeliveryAttempt.delivery_id == d.id)
        .order_by(DeliveryAttempt.attempt_number.desc())
        .limit(1)
    )
    return {
        "dead_letter": dl,
        "delivery": d,
        "event_type": db.get(Event, d.event_id).event_type,
        "endpoint_name": db.get(Endpoint, d.endpoint_id).name,
        "last_status": last,
        "replays": list(
            db.scalars(select(Delivery).where(Delivery.replay_of_delivery_id == d.id).order_by(Delivery.created_at))
        ),
    }


def resolve_dead_letter(db: Session, ws: Workspace, actor: str, record_id: uuid.UUID, note: str) -> DeadLetter:
    dl = db.scalar(
        select(DeadLetter).where(DeadLetter.id == record_id, DeadLetter.workspace_id == ws.id).with_for_update()
    )
    if dl is None:
        raise not_found("Dead-letter record")
    if dl.resolved_at is not None:
        raise ServiceError(409, "already_resolved", "This dead-letter record is already resolved.")
    dl.resolved_at = func.now()
    dl.resolution_note = note.strip() or "Dismissed"
    audit(db, ws.id, actor, "dead_letter.resolved", "dead_letter", dl.id, note=dl.resolution_note[:200])
    db.commit()
    db.refresh(dl)
    return dl


def dead_letter_counts(db: Session, ws: Workspace) -> dict[str, int]:
    row = db.execute(
        select(
            func.count().filter(DeadLetter.resolved_at.is_(None)),
            func.count().filter(DeadLetter.resolved_at.is_not(None)),
        ).where(DeadLetter.workspace_id == ws.id)
    ).one()
    return {"open": row[0], "resolved": row[1]}
