"""Guest data retention: expired workspaces are deleted for real, on a durable schedule."""

import logging

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import IdempotencyRecord, Job, WorkerHeartbeat, Workspace

log = logging.getLogger("retention")
BATCH = 200

# One cleanup job per interval bucket, no matter how many workers try to schedule it.
SCHEDULE_SQL = text(
    """
    INSERT INTO jobs (id, job_type, payload, dedupe_key, status, scheduled_at, claim_count, created_at, updated_at)
    VALUES (gen_random_uuid(), 'cleanup', '{}'::jsonb,
            'cleanup:' || floor(extract(epoch FROM now()) / :interval)::bigint, 'queued', now(), 0, now(), now())
    ON CONFLICT (dedupe_key) DO NOTHING
    """
)


def schedule_cleanup(db: Session) -> None:
    db.execute(SCHEDULE_SQL, {"interval": get_settings().cleanup_interval_seconds})
    db.commit()


def run_cleanup(db: Session, payload: dict | None = None) -> str:
    """Idempotent: safe to run concurrently or repeatedly. Returns a short summary."""
    workspaces = 0
    while True:
        # Guest workspaces past their inactivity window; FK cascades remove all owned rows.
        ids = list(
            db.scalars(
                select(Workspace.id)
                .where(Workspace.workspace_type == "guest", Workspace.expires_at < text("now()"))
                .limit(BATCH)
                .with_for_update(skip_locked=True)
            )
        )
        if not ids:
            break
        db.execute(delete(Workspace).where(Workspace.id.in_(ids)))
        db.commit()
        workspaces += len(ids)
    keys = db.execute(delete(IdempotencyRecord).where(IdempotencyRecord.expires_at < text("now()"))).rowcount
    jobs = db.execute(
        delete(Job).where(Job.status.in_(("succeeded", "failed")), Job.completed_at < text("now() - interval '1 day'"))
    ).rowcount
    db.execute(delete(WorkerHeartbeat).where(WorkerHeartbeat.last_seen_at < text("now() - interval '1 day'")))
    db.commit()
    summary = f"workspaces={workspaces} idempotency_keys={keys} jobs={jobs}"
    if workspaces or keys or jobs:
        log.info("cleanup removed %s", summary)
    return summary
