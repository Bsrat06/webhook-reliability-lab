from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_token, new_session_token
from app.models import GuestSession, Workspace
from app.services import endpoints as endpoint_service


def create_guest_session(db: Session) -> tuple[Workspace, str]:
    """Create an isolated guest workspace with a ready-to-use built-in receiver."""
    settings = get_settings()
    expires = datetime.now(UTC) + timedelta(hours=settings.guest_retention_hours)
    ws = Workspace(name="Guest workspace", workspace_type="guest", expires_at=expires)
    db.add(ws)
    db.flush()
    token = new_session_token()
    db.add(GuestSession(workspace_id=ws.id, token_hash=hash_token(token), expires_at=expires))
    endpoint_service.create_endpoint(
        db,
        ws,
        name="Built-in receiver",
        description="Platform-hosted receiver. Configure its responses to simulate failures.",
        endpoint_type="builtin",
    )
    db.commit()
    return ws, token


def reset_workspace(db: Session, ws: Workspace, actor: str) -> None:
    """Delete all workspace data and start over with one default receiver. The session and audit log remain."""
    from sqlalchemy import delete, select

    from app.models import Endpoint, Event, IdempotencyRecord, Job, SimulationRun
    from app.services.audit import audit

    # Lock the workspace row so a concurrent reset or run creation waits for this one.
    db.scalar(select(Workspace.id).where(Workspace.id == ws.id).with_for_update())
    db.execute(delete(Job).where(Job.workspace_id == ws.id))
    db.execute(delete(Event).where(Event.workspace_id == ws.id))  # cascades deliveries, attempts, transitions, DLQ
    db.execute(delete(SimulationRun).where(SimulationRun.workspace_id == ws.id))
    db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.workspace_id == ws.id))
    db.execute(delete(Endpoint).where(Endpoint.workspace_id == ws.id))
    endpoint_service.create_endpoint(
        db,
        ws,
        name="Built-in receiver",
        description="Platform-hosted receiver. Configure its responses to simulate failures.",
        endpoint_type="builtin",
    )
    audit(db, ws.id, actor, "workspace.reset", "workspace", ws.id)
    db.commit()


def delete_workspace(db: Session, ws: Workspace) -> None:
    """Immediate deletion of the guest workspace and everything in it (no need to wait for retention)."""
    from sqlalchemy import delete

    db.execute(delete(Workspace).where(Workspace.id == ws.id))
    db.commit()
