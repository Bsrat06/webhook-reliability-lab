import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ServiceError, not_found, quota_exceeded
from app.core.security import encrypt_secret, new_endpoint_secret
from app.domain.receiver import ReceiverBehavior
from app.domain.retry import DEFAULT_POLICY, RetryPolicySpec
from app.models import Endpoint, IdempotencyRecord, Workspace
from app.services.audit import audit


def get_endpoint(db: Session, ws: Workspace, endpoint_id: uuid.UUID) -> Endpoint:
    ep = db.scalar(select(Endpoint).where(Endpoint.id == endpoint_id, Endpoint.workspace_id == ws.id))
    if ep is None:
        raise not_found("Endpoint")
    return ep


def list_endpoints(db: Session, ws: Workspace) -> list[Endpoint]:
    return list(db.scalars(select(Endpoint).where(Endpoint.workspace_id == ws.id).order_by(Endpoint.created_at)))


def create_endpoint(
    db: Session,
    ws: Workspace,
    *,
    name: str,
    description: str = "",
    endpoint_type: str = "builtin",
    destination_url: str | None = None,
    receiver_config: ReceiverBehavior | None = None,
    retry_policy: RetryPolicySpec | None = None,
) -> tuple[Endpoint, str]:
    """Returns the endpoint and its plaintext signing secret (shown to the user once)."""
    limit = get_settings().quota_endpoints
    count = db.scalar(select(func.count()).select_from(Endpoint).where(Endpoint.workspace_id == ws.id))
    if count >= limit:
        raise quota_exceeded("Endpoint", limit, "Delete an unused endpoint to create another.")
    if endpoint_type == "external":
        # External destinations stay disabled until SSRF defenses are implemented and tested (spec §8.3).
        raise ServiceError(
            422, "external_disabled", "External HTTPS destinations are not enabled on this deployment yet."
        )
    secret = new_endpoint_secret()
    ep = Endpoint(
        workspace_id=ws.id,
        name=name,
        description=description,
        endpoint_type=endpoint_type,
        destination_url=destination_url,
        secret_ciphertext=encrypt_secret(secret),
        receiver_config=(receiver_config or ReceiverBehavior()).model_dump(),
        retry_policy=(retry_policy or DEFAULT_POLICY).model_dump(),
    )
    db.add(ep)
    db.flush()
    return ep, secret


def update_endpoint(
    db: Session, ws: Workspace, endpoint_id: uuid.UUID, changes: dict, actor: str = "system"
) -> Endpoint:
    ep = get_endpoint(db, ws, endpoint_id)
    for field in ("name", "description", "enabled"):
        if changes.get(field) is not None:
            setattr(ep, field, changes[field])
    if changes.get("receiver_config") is not None:
        ep.receiver_config = ReceiverBehavior.model_validate(changes["receiver_config"]).model_dump()
    if changes.get("retry_policy") is not None:
        ep.retry_policy = RetryPolicySpec.model_validate(changes["retry_policy"]).model_dump()
    audit(
        db,
        ws.id,
        actor,
        "endpoint.updated",
        "endpoint",
        ep.id,
        fields=sorted(k for k, v in changes.items() if v is not None),
    )
    db.commit()
    db.refresh(ep)
    return ep


def rotate_secret(db: Session, ws: Workspace, endpoint_id: uuid.UUID, actor: str = "system") -> str:
    ep = get_endpoint(db, ws, endpoint_id)
    secret = new_endpoint_secret()
    ep.secret_ciphertext = encrypt_secret(secret)
    audit(db, ws.id, actor, "endpoint.secret_rotated", "endpoint", ep.id)
    db.commit()
    return secret


def delete_endpoint(db: Session, ws: Workspace, endpoint_id: uuid.UUID, actor: str = "system") -> None:
    ep = get_endpoint(db, ws, endpoint_id)
    audit(db, ws.id, actor, "endpoint.deleted", "endpoint", ep.id, name=ep.name)
    db.delete(ep)
    db.commit()


def idempotency_records(db: Session, ws: Workspace, endpoint_id: uuid.UUID, limit: int) -> list[IdempotencyRecord]:
    get_endpoint(db, ws, endpoint_id)
    return list(
        db.scalars(
            select(IdempotencyRecord)
            .where(IdempotencyRecord.workspace_id == ws.id, IdempotencyRecord.endpoint_id == endpoint_id)
            .order_by(IdempotencyRecord.last_seen_at.desc())
            .limit(limit)
        )
    )
