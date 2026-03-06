from datetime import timedelta

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import func, select

from app.api.deps import DB, CurrentWorkspace, current_workspace
from app.api.ratelimit import guest_session_limiter
from app.core.config import get_settings
from app.core.errors import ServiceError
from app.models import AuditEvent, Endpoint, Event, Job, WorkerHeartbeat
from app.schemas import AuditOut, SessionOut, WorkspaceOut
from app.services.guest import create_guest_session

router = APIRouter(prefix="/api/v1", tags=["session"])


def _session_payload(db, ws) -> SessionOut:
    s = get_settings()
    endpoints = db.scalar(select(func.count()).select_from(Endpoint).where(Endpoint.workspace_id == ws.id))
    events_hour = db.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.workspace_id == ws.id, Event.created_at >= func.now() - timedelta(hours=1))
    )
    last_beat = db.scalar(select(func.max(WorkerHeartbeat.last_seen_at)))
    db_now = db.scalar(select(func.now()))
    queued = db.execute(
        select(func.count(), func.min(Job.scheduled_at)).where(
            Job.workspace_id == ws.id, Job.status == "queued", Job.scheduled_at <= func.now()
        )
    ).one()
    worker_online = last_beat is not None and db_now - last_beat < timedelta(seconds=20)
    return SessionOut(
        workspace=WorkspaceOut.model_validate(ws),
        retention_hours=s.guest_retention_hours,
        limits={
            "endpoints": s.quota_endpoints,
            "payload_bytes": s.quota_payload_bytes,
            "events_per_hour": s.quota_events_per_hour,
        },
        usage={"endpoints": endpoints, "events_last_hour": events_hour},
        worker={
            "online": worker_online,
            "last_heartbeat_at": last_beat.isoformat() if last_beat else None,
            "due_jobs": queued[0],
            "oldest_due_seconds": int((db_now - queued[1]).total_seconds()) if queued[1] else 0,
        },
    )


@router.post("/guest/session", response_model=SessionOut, status_code=201)
def start_guest_session(request: Request, response: Response, db: DB) -> SessionOut:
    s = get_settings()
    # Reuse a still-valid session instead of minting a new workspace on every click.
    if request.cookies.get(s.session_cookie_name):
        try:
            ws = current_workspace(request, db)
            response.status_code = 200
            return _session_payload(db, ws)
        except Exception:
            pass
    client_ip = request.client.host if request.client else "unknown"
    if not guest_session_limiter.allow(request.headers.get("x-forwarded-for", client_ip).split(",")[0].strip()):
        raise ServiceError(429, "rate_limited", "Too many guest sessions from this address. Try again in a few minutes.")
    ws, token = create_guest_session(db)
    response.set_cookie(
        s.session_cookie_name,
        token,
        max_age=s.guest_retention_hours * 3600,
        httponly=True,
        secure=s.is_production,
        samesite="lax",
        path="/",
    )
    return _session_payload(db, ws)


@router.get("/session", response_model=SessionOut)
def get_session(ws: CurrentWorkspace, db: DB) -> SessionOut:
    return _session_payload(db, ws)


@router.get("/workspaces/current/audit", response_model=list[AuditOut])
def list_audit(ws: CurrentWorkspace, db: DB, limit: int = Query(50, ge=1, le=200), before_id: int | None = None):
    stmt = select(AuditEvent).where(AuditEvent.workspace_id == ws.id)
    if before_id:
        stmt = stmt.where(AuditEvent.id < before_id)
    return list(db.scalars(stmt.order_by(AuditEvent.id.desc()).limit(limit)))
