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
