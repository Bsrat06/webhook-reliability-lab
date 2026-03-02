from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_token
from app.db.session import get_db
from app.models import GuestSession, Workspace

DB = Annotated[Session, Depends(get_db)]


def api_error(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def current_workspace(request: Request, db: DB) -> Workspace:
    """Resolve the workspace solely from the session cookie; never from client-supplied IDs."""
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise api_error(401, "unauthenticated", "No active session. Start a guest session first.")
    now = datetime.now(UTC)
    row = db.execute(
        select(GuestSession, Workspace)
        .join(Workspace, Workspace.id == GuestSession.workspace_id)
        .where(GuestSession.token_hash == hash_token(token), GuestSession.revoked_at.is_(None))
    ).first()
    if row is None or row.GuestSession.expires_at <= now:
        raise api_error(401, "session_expired", "Your session has expired. Start a new guest session.")
    sess, ws = row.GuestSession, row.Workspace
    # Sliding inactivity window; throttle writes to once a minute.
    if now - sess.last_seen_at > timedelta(seconds=60):
        expiry = now + timedelta(hours=settings.guest_retention_hours)
        db.execute(update(GuestSession).where(GuestSession.id == sess.id).values(last_seen_at=now, expires_at=expiry))
        db.execute(update(Workspace).where(Workspace.id == ws.id).values(last_active_at=now, expires_at=expiry))
        db.commit()
    return ws


CurrentWorkspace = Annotated[Workspace, Depends(current_workspace)]
