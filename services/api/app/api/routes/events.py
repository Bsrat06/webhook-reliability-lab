import uuid
from datetime import datetime

from fastapi import APIRouter, Query

from app.api.deps import DB, CurrentWorkspace
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


@router.get("", response_model=EventList)
def list_events(
    ws: CurrentWorkspace,
    db: DB,
    limit: int = Query(25, ge=1, le=100),
    before: datetime | None = None,
    event_type: str | None = Query(None, max_length=120),
    q: str | None = Query(None, max_length=200),
):
    rows = svc.list_events(db, ws, limit=limit, before=before, event_type=event_type, q=q)
    items = [
        EventListItem(
            id=e.id,
            event_type=e.event_type,
            created_at=e.created_at,
            idempotency_key=e.idempotency_key,
            correlation_id=e.correlation_id,
            source=e.source,
            status=svc.aggregate_status(ds),
            deliveries=[DeliverySummary.model_validate(d) for d in ds],
        )
        for e, ds in rows
    ]
    return EventList(items=items, next_before=items[-1].created_at if len(items) == limit else None)


@router.get("/{event_id}", response_model=EventDetail)
def get_event(event_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    return _detail(svc.get_event_detail(db, ws, event_id))


def _detail(data: dict) -> EventDetail:
    ev = data["event"]
    dead = {d.delivery_id: d.reason for d in data["dead_letters"]}
    return EventDetail(
        id=ev.id,
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
