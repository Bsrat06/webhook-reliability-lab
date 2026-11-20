import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query

from app.api.deps import DB, Actor, CurrentWorkspace
from app.core.config import get_settings
from app.core.errors import ServiceError
from app.schemas import (
    AttemptOut,
    BulkReplayIn,
    DeadLetterDetail,
    DeadLetterItem,
    DeadLetterList,
    DeliverySummary,
    ReplayIn,
    ReplayOut,
    ReplayRef,
    ResolveIn,
)
from app.services import deliveries as svc

router = APIRouter(prefix="/api/v1", tags=["deliveries"])


@router.post("/deliveries/{delivery_id}/replay", response_model=ReplayOut, status_code=201)
def replay_delivery(delivery_id: uuid.UUID, body: ReplayIn, ws: CurrentWorkspace, actor: Actor, db: DB):
    replays = svc.replay_deliveries(db, ws, actor, [delivery_id], policy=body.policy, custom_policy=body.retry_policy)
    return ReplayOut(replays=[DeliverySummary.model_validate(r) for r in replays])


@router.post("/deliveries/replay", response_model=ReplayOut, status_code=201)
def bulk_replay(body: BulkReplayIn, ws: CurrentWorkspace, actor: Actor, db: DB):
    limit = get_settings().quota_bulk_replay
    n = len(set(body.delivery_ids))
    if n > limit:
        raise ServiceError(422, "bulk_limit", f"Replay at most {limit} deliveries at once (selected {n}).", limit=limit)
    if body.confirm_count != n:
        raise ServiceError(
            422, "confirmation_required", f"Confirm the bulk replay by sending confirm_count={n}.", expected=n
        )
    replays = svc.replay_deliveries(
        db, ws, actor, body.delivery_ids, policy=body.policy, custom_policy=body.retry_policy
    )
    return ReplayOut(replays=[DeliverySummary.model_validate(r) for r in replays])


@router.post("/deliveries/{delivery_id}/cancel", response_model=DeliverySummary)
def cancel_delivery(delivery_id: uuid.UUID, ws: CurrentWorkspace, actor: Actor, db: DB):
    return DeliverySummary.model_validate(svc.cancel_delivery(db, ws, actor, delivery_id))


def _item(i: dict) -> dict:
    dl, d = i["dead_letter"], i["delivery"]
    return dict(
        id=dl.id,
        delivery_id=d.id,
        event_id=d.event_id,
        event_type=i["event_type"],
        endpoint_id=d.endpoint_id,
        endpoint_name=i["endpoint_name"],
        reason=dl.reason,
        attempt_count=d.attempt_count,
        last_error_category=d.last_error_category,
        last_status=i["last_status"],
        created_at=dl.created_at,
        resolved_at=dl.resolved_at,
        resolution_note=dl.resolution_note,
        replays=[ReplayRef.model_validate(r) for r in i["replays"]],
    )


@router.get("/dead-letters", response_model=DeadLetterList)
def list_dead_letters(
    ws: CurrentWorkspace,
    db: DB,
    state: Literal["open", "resolved", "all"] = "open",
    endpoint_id: uuid.UUID | None = None,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
    before: datetime | None = None,
):
    items = svc.list_dead_letters(db, ws, state=state, endpoint_id=endpoint_id, q=q, limit=limit, before=before)
    out = [DeadLetterItem(**_item(i)) for i in items]
    return DeadLetterList(
        items=out,
        counts=svc.dead_letter_counts(db, ws),
        next_before=out[-1].created_at if len(out) == limit else None,
    )


@router.get("/dead-letters/{record_id}", response_model=DeadLetterDetail)
def get_dead_letter(record_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    i = svc.dead_letter_detail(db, ws, record_id)
    return DeadLetterDetail(
        **_item(i),
        payload=i["payload"],
        retry_policy=i["delivery"].retry_policy,
        attempts=[AttemptOut.model_validate(a) for a in i["attempts"]],
    )


@router.post("/dead-letters/{record_id}/replay", response_model=ReplayOut, status_code=201)
def replay_dead_letter(record_id: uuid.UUID, body: ReplayIn, ws: CurrentWorkspace, actor: Actor, db: DB):
    dl = svc.get_dead_letter(db, ws, record_id)
    replays = svc.replay_deliveries(
        db, ws, actor, [dl.delivery_id], policy=body.policy, custom_policy=body.retry_policy
    )
    return ReplayOut(replays=[DeliverySummary.model_validate(r) for r in replays])


@router.post("/dead-letters/{record_id}/resolve", response_model=DeadLetterItem)
def resolve_dead_letter(record_id: uuid.UUID, body: ResolveIn, ws: CurrentWorkspace, actor: Actor, db: DB):
    dl = svc.resolve_dead_letter(db, ws, actor, record_id, body.note)
    return DeadLetterItem(**_item(svc.dead_letter_detail(db, ws, dl.id)))
