import uuid

from fastapi import APIRouter, Response
from sqlalchemy import func, select

from app.api.deps import DB, CurrentWorkspace
from app.models import Delivery
from app.schemas import EndpointCreated, EndpointIn, EndpointOut, EndpointPatch
from app.services import endpoints as svc

router = APIRouter(prefix="/api/v1/endpoints", tags=["endpoints"])


def _with_stats(db, ws, eps) -> list[EndpointOut]:
    ids = [e.id for e in eps]
    stats: dict[uuid.UUID, dict[str, int]] = {i: {} for i in ids}
    if ids:
        rows = db.execute(
            select(Delivery.endpoint_id, Delivery.status, func.count())
            .where(Delivery.workspace_id == ws.id, Delivery.endpoint_id.in_(ids))
            .group_by(Delivery.endpoint_id, Delivery.status)
        )
        for ep_id, status, n in rows:
            stats[ep_id][status] = n
    return [EndpointOut.model_validate(e).model_copy(update={"stats": stats[e.id]}) for e in eps]


@router.get("", response_model=list[EndpointOut])
def list_endpoints(ws: CurrentWorkspace, db: DB):
    return _with_stats(db, ws, svc.list_endpoints(db, ws))


@router.post("", response_model=EndpointCreated, status_code=201)
def create_endpoint(body: EndpointIn, ws: CurrentWorkspace, db: DB):
    ep, secret = svc.create_endpoint(
        db,
        ws,
        name=body.name,
        description=body.description,
        endpoint_type=body.endpoint_type,
        destination_url=body.destination_url,
        receiver_config=body.receiver_config,
        retry_policy=body.retry_policy,
    )
    db.commit()
    return EndpointCreated(endpoint=_with_stats(db, ws, [ep])[0], signing_secret=secret)


@router.get("/{endpoint_id}", response_model=EndpointOut)
def get_endpoint(endpoint_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    return _with_stats(db, ws, [svc.get_endpoint(db, ws, endpoint_id)])[0]


@router.patch("/{endpoint_id}", response_model=EndpointOut)
def patch_endpoint(endpoint_id: uuid.UUID, body: EndpointPatch, ws: CurrentWorkspace, db: DB):
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    for k in ("receiver_config", "retry_policy"):
        if changes.get(k) is not None:
            changes[k] = changes[k].model_dump()
    return _with_stats(db, ws, [svc.update_endpoint(db, ws, endpoint_id, changes)])[0]


@router.post("/{endpoint_id}/rotate-secret")
def rotate_secret(endpoint_id: uuid.UUID, ws: CurrentWorkspace, db: DB) -> dict:
    return {"signing_secret": svc.rotate_secret(db, ws, endpoint_id)}


@router.delete("/{endpoint_id}", status_code=204)
def delete_endpoint(endpoint_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    svc.delete_endpoint(db, ws, endpoint_id)
    return Response(status_code=204)
