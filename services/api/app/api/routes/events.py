import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query

from app.api.deps import DB, CurrentWorkspace
from app.core.errors import ServiceError
from app.domain.retry import RetryPolicySpec
from app.schemas import (
    AttemptOut,
    DeliveryOut,
    DeliverySummary,
    EventDetail,
    EventIn,
    EventList,
    EventListItem,
    TransitionOut,
)
from app.services import events as svc

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.post("", response_model=EventDetail, status_code=201)
def create_event(body: EventIn, ws: CurrentWorkspace, db: DB):
    event = svc.create_event(
        db,
        ws,
        event_type=body.event_type,
        payload=body.payload,
        endpoint_ids=body.endpoint_ids,
        idempotency_key=body.idempotency_key,
        metadata=body.metadata,
    )
    return _detail(svc.get_event_detail(db, ws, event.id))


DeliveryFilter = Literal[
    "pending", "processing", "retry_scheduled", "succeeded", "dead_lettered", "cancelled", "retried"
]


def parse_cursor(cursor: str | None) -> tuple[datetime, uuid.UUID] | None:
    if not cursor:
        return None
    try:
        ts, _, ident = cursor.partition("|")
        return datetime.fromisoformat(ts), uuid.UUID(ident)
    except ValueError as exc:
        raise ServiceError(422, "invalid_cursor", "Invalid pagination cursor.") from exc


def event_page(db, ws, limit: int, **filters) -> EventList:
    rows = svc.list_events(db, ws, limit=limit, **filters)
    items = [
        EventListItem(
            id=e.id,
            event_type=e.event_type,
            created_at=e.created_at,
            idempotency_key=e.idempotency_key,
            correlation_id=e.correlation_id,
            source=e.source,
            simulation_run_id=e.simulation_run_id,
            status=svc.aggregate_status(ds),
            deliveries=[DeliverySummary.model_validate(d) for d in ds],
        )
        for e, ds in rows
    ]
    last = items[-1] if len(items) == limit else None
    return EventList(items=items, next_cursor=f"{last.created_at.isoformat()}|{last.id}" if last else None)


@router.get("", response_model=EventList)
def list_events(
    ws: CurrentWorkspace,
    db: DB,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=100),
    event_type: str | None = Query(None, max_length=120),
    q: str | None = Query(None, max_length=200),
    source: Literal["manual", "simulation"] | None = None,
    simulation_run_id: uuid.UUID | None = None,
    delivery_status: DeliveryFilter | None = None,
):
    return event_page(
        db,
        ws,
        limit,
        cursor=parse_cursor(cursor),
        event_type=event_type,
        q=q,
        source=source,
        simulation_run_id=simulation_run_id,
        delivery_status=delivery_status,
    )


@router.get("/{event_id}", response_model=EventDetail)
def get_event(event_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    return _detail(svc.get_event_detail(db, ws, event_id))


def _detail(data: dict) -> EventDetail:
    ev = data["event"]
    dead = {d.delivery_id: d.reason for d in data["dead_letters"]}
    return EventDetail(
        id=ev.id,
        simulation_run_id=ev.simulation_run_id,
        event_type=ev.event_type,
        payload=ev.payload,
        metadata=ev.metadata_,
        idempotency_key=ev.idempotency_key,
        correlation_id=ev.correlation_id,
        source=ev.source,
        created_at=ev.created_at,
        status=svc.aggregate_status(data["deliveries"]),
        deliveries=[
            DeliveryOut(
                **DeliverySummary.model_validate(d).model_dump(),
                endpoint_name=data["endpoints"][d.endpoint_id].name,
                retry_policy=RetryPolicySpec.model_validate(d.retry_policy).model_dump(),
                replay_of_delivery_id=d.replay_of_delivery_id,
                created_at=d.created_at,
                dead_letter_reason=dead.get(d.id),
            )
            for d in data["deliveries"]
        ],
        attempts=[AttemptOut.model_validate(a) for a in data["attempts"]],
        transitions=[TransitionOut.model_validate(t) for t in data["transitions"]],
    )
