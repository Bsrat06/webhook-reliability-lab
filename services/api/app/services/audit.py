import uuid

from sqlalchemy.orm import Session

from app.models import AuditEvent


def audit(
    db: Session,
    workspace_id: uuid.UUID,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: object = None,
    **metadata: object,
) -> None:
    """Record a security-relevant action. Metadata must never contain secrets or payloads."""
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            metadata_=metadata,
        )
    )
