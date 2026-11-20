"""Simulation runs: real events and deliveries executed by the worker, measured from persisted records."""

import uuid
from datetime import timedelta

from sqlalchemy import func, insert, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import ServiceError, not_found, quota_exceeded
from app.domain.simulation import PRESETS, SimulationConfig, is_duplicate_send, simulation_key
from app.models import (
    TERMINAL_DELIVERY_STATUSES,
    Delivery,
    DeliveryTransition,
    Endpoint,
    Event,
    Job,
    SimulationRun,
    Workspace,
)
from app.services.audit import audit

MIN_PERCENTILE_SAMPLES = 20
_NON_TERMINAL = ("pending", "processing", "retry_scheduled")


# ---- Creation ------------------------------------------------------------------------------------------


def _check_quotas(db: Session, ws: Workspace, cfg: SimulationConfig) -> None:
    s = get_settings()
    if cfg.event_count > s.quota_simulation_events:
        raise quota_exceeded("Events per simulation", s.quota_simulation_events, "Lower the event count.")
    if cfg.concurrency > s.quota_workspace_concurrency:
        raise quota_exceeded("Simulation concurrency", s.quota_workspace_concurrency, "Lower the concurrency.")
    if cfg.max_runtime_s > s.quota_simulation_runtime_seconds:
        raise quota_exceeded(
            "Simulation runtime (seconds)", s.quota_simulation_runtime_seconds, "Lower the max runtime."
        )
    # Serialize run creation per workspace so two concurrent requests cannot both pass the active-run check.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"sim:{ws.id}"})
    active = db.scalar(
        select(func.count())
        .select_from(SimulationRun)
        .where(SimulationRun.workspace_id == ws.id, SimulationRun.status == "running")
    )
    if active >= s.quota_concurrent_simulations:
        raise ServiceError(
            409,
            "simulation_active",
            f"A workspace can run {s.quota_concurrent_simulations} simulation at a time. Wait for it to finish or cancel it.",
            limit=s.quota_concurrent_simulations,
        )
    running = db.scalar(select(func.count()).select_from(SimulationRun).where(SimulationRun.status == "running"))
    if running >= s.global_concurrent_simulations:
        raise ServiceError(
            429, "capacity", "The shared demo is at its simulation capacity right now. Try again in a minute."
        )
    recent = db.scalar(
        select(func.coalesce(func.sum(SimulationRun.event_count), 0)).where(
            SimulationRun.workspace_id == ws.id, SimulationRun.created_at >= func.now() - timedelta(hours=1)
        )
    )
    if recent + cfg.event_count > s.quota_simulation_events_per_hour:
        raise quota_exceeded(
            "Simulated events per hour", s.quota_simulation_events_per_hour, f"{recent} used in the last hour."
        )


def create_run(
    db: Session, ws: Workspace, actor: str, cfg: SimulationConfig, name: str | None, preset: str | None
) -> SimulationRun:
    if preset is not None and preset not in PRESETS:
        raise ServiceError(422, "unknown_preset", f"Unknown preset '{preset}'.")
    endpoints = {
        e.id: e
        for e in db.scalars(select(Endpoint).where(Endpoint.workspace_id == ws.id, Endpoint.id.in_(cfg.endpoint_ids)))
    }
    if len(endpoints) != len(cfg.endpoint_ids):
        raise ServiceError(422, "invalid_endpoints", "Select endpoints from this workspace.")
    for e in endpoints.values():
        if e.endpoint_type != "builtin":
            raise ServiceError(
                422, "builtin_only", f"Simulations target built-in receivers only ('{e.name}' is external)."
            )
        if not e.enabled:
            raise ServiceError(422, "endpoint_disabled", f"Endpoint '{e.name}' is disabled.")

    duplicates = [i for i in range(cfg.event_count) if is_duplicate_send(cfg.seed, i, cfg.duplicate_send_rate)]
    total_events = cfg.event_count + len(duplicates)
    total_deliveries = total_events * len(cfg.endpoint_ids)
    _check_quotas(db, ws, cfg)

    now = db.scalar(select(func.now()))
    run = SimulationRun(
        workspace_id=ws.id,
        name=(name or (PRESETS[preset][0] if preset else "Custom simulation"))[:120],
        preset=preset,
        status="running",
        configuration=cfg.model_dump(mode="json"),
        random_seed=cfg.seed,
        event_count=total_events,
        delivery_count=total_deliveries,
        started_at=now,
        deadline_at=now + timedelta(seconds=cfg.max_runtime_s),
    )
    db.add(run)
    db.flush()

    policy = cfg.retry_policy.model_dump()
    limit = min(cfg.concurrency, get_settings().quota_workspace_concurrency)
    events, deliveries, transitions, jobs = [], [], [], []
    sends = [(i, False) for i in range(cfg.event_count)] + [(i, True) for i in duplicates]
    for i, dup in sends:
        offset_ms = int(i * cfg.emit_over_ms / cfg.event_count) + (50 if dup else 0)
        event_id = uuid.uuid4()
        events.append(
            {
                "id": event_id,
                "workspace_id": ws.id,
                "event_type": cfg.event_type,
                "payload": {"simulation": run.name, "index": i, "order_id": f"ord_{cfg.seed}_{i:05d}"},
                "metadata_": {"simulation_index": str(i), **({"producer_duplicate": "true"} if dup else {})},
                "idempotency_key": f"sim-{str(run.id)[:8]}-{i}" if cfg.use_idempotency_keys else None,
                "correlation_id": uuid.uuid4().hex,
                "source": "simulation",
                "simulation_run_id": run.id,
            }
        )
        for ep_index, ep_id in enumerate(cfg.endpoint_ids):
            delivery_id = uuid.uuid4()
            deliveries.append(
                {
                    "id": delivery_id,
                    "workspace_id": ws.id,
                    "event_id": event_id,
                    "endpoint_id": ep_id,
                    "status": "pending",
                    "retry_policy": policy,
                    "attempt_count": 0,
                    "simulation_run_id": run.id,
                    "simulation_key": simulation_key(cfg.seed, i, ep_index, dup),
                }
            )
            transitions.append(
                {
                    "workspace_id": ws.id,
                    "delivery_id": delivery_id,
                    "from_status": None,
                    "to_status": "pending",
                    "reason": "created by simulation",
                }
            )
            jobs.append(
                {
                    "workspace_id": ws.id,
                    "job_type": "deliver",
                    "payload": {"delivery_id": str(delivery_id)},
                    "dedupe_key": f"deliver:{delivery_id}:1",
                    "status": "queued",
                    "scheduled_at": now + timedelta(milliseconds=offset_ms),
                    "claim_count": 0,
                    "concurrency_key": f"ws:{ws.id}",
                    "concurrency_limit": limit,
                }
            )
    db.execute(insert(Event), events)
    db.execute(insert(Delivery), deliveries)
    db.execute(insert(DeliveryTransition), transitions)
    db.execute(insert(Job), jobs)
    # Durable watchdog: enforces the runtime limit even if nobody is watching the run.
    db.add(
        Job(
            workspace_id=ws.id,
            job_type="simulation_deadline",
            payload={"run_id": str(run.id)},
            dedupe_key=f"simulation_deadline:{run.id}",
            scheduled_at=run.deadline_at,
        )
    )
    audit(
        db,
        ws.id,
        actor,
        "simulation.started",
        "simulation_run",
        run.id,
        preset=preset,
        events=total_events,
        seed=cfg.seed,
    )
    db.commit()
    return run


# ---- Measurement ---------------------------------------------------------------------------------------

SUMMARY_SQL = text(
    """
    WITH d AS (SELECT * FROM deliveries WHERE simulation_run_id = :id),
    a AS (SELECT a.* FROM delivery_attempts a JOIN d ON d.id = a.delivery_id)
    SELECT
      (SELECT count(*) FROM a) AS attempts,
      (SELECT count(*) FROM d WHERE attempt_count > 0) AS attempted,
      (SELECT percentile_cont(ARRAY[0.5, 0.95, 0.99]) WITHIN GROUP (ORDER BY duration_ms) FROM a) AS latency,
      (SELECT avg(duration_ms) FROM a) AS latency_mean,
      (SELECT count(*) FROM a WHERE response_headers->>'x-receiver-result' = 'duplicate') AS duplicates,
      (SELECT percentile_cont(ARRAY[0.5, 0.95, 0.99]) WITHIN GROUP (
          ORDER BY extract(epoch FROM completed_at - created_at) * 1000) FROM d WHERE status = 'succeeded') AS delivery_time,
      (SELECT count(*) FROM d WHERE status = 'succeeded') AS delivery_time_n
    """
)


def _pcts(values, n: int) -> dict:
    if n < MIN_PERCENTILE_SAMPLES or not values:
        return {"n": n, "p50": None, "p95": None, "p99": None, "insufficient": True}
    return {"n": n, "p50": round(values[0]), "p95": round(values[1]), "p99": round(values[2]), "insufficient": False}


def compute_summary(db: Session, run: SimulationRun) -> dict:
    """All values derive from persisted deliveries and attempts. Rates use explicit denominators."""
    by_status = dict(
        db.execute(
            select(Delivery.status, func.count()).where(Delivery.simulation_run_id == run.id).group_by(Delivery.status)
        ).all()
    )
    row = db.execute(SUMMARY_SQL, {"id": run.id}).one()
    categories = dict(
        db.execute(
            text(
                "SELECT coalesce(a.error_category, 'success'), count(*) FROM delivery_attempts a "
                "JOIN deliveries d ON d.id = a.delivery_id WHERE d.simulation_run_id = :id GROUP BY 1"
            ),
            {"id": run.id},
        ).all()
    )
    distribution = {
        str(k): v
        for k, v in db.execute(
            select(Delivery.attempt_count, func.count())
            .where(Delivery.simulation_run_id == run.id, Delivery.status.in_(TERMINAL_DELIVERY_STATUSES))
            .group_by(Delivery.attempt_count)
            .order_by(Delivery.attempt_count)
        ).all()
    }
    succeeded = by_status.get("succeeded", 0)
    dead = by_status.get("dead_lettered", 0)
    cancelled = by_status.get("cancelled", 0)
    decided = succeeded + dead
    finished = decided + cancelled
    end = run.completed_at or db.scalar(select(func.now()))
    return {
        "deliveries": run.delivery_count,
        "events": run.event_count,
        "by_status": {
            s: by_status.get(s, 0)
            for s in ("pending", "processing", "retry_scheduled", "succeeded", "dead_lettered", "cancelled")
        },
        "finished": finished,
        "progress": round(finished / run.delivery_count, 4) if run.delivery_count else 0,
        # Denominator: deliveries that reached a decision (succeeded or dead-lettered); cancelled are excluded.
        "success_rate": round(succeeded / decided, 4) if decided else None,
        "dead_letter_rate": round(dead / decided, 4) if decided else None,
        "attempts": row.attempts,
        "retries": row.attempts - row.attempted,
        "attempts_per_delivery": round(row.attempts / row.attempted, 3) if row.attempted else None,
        "duplicates_suppressed": row.duplicates,
        "failure_categories": categories,
        "attempt_distribution": distribution,
        "latency_ms": {
            **_pcts(row.latency, row.attempts),
            "mean": round(float(row.latency_mean)) if row.latency_mean is not None else None,
        },
        "delivery_time_ms": _pcts(row.delivery_time, row.delivery_time_n),
        "elapsed_ms": max(0, int((end - run.started_at).total_seconds() * 1000)),
    }


# ---- Lifecycle -----------------------------------------------------------------------------------------


def _remaining(db: Session, run_id: uuid.UUID) -> int:
    return db.scalar(
        select(func.count())
        .select_from(Delivery)
        .where(Delivery.simulation_run_id == run_id, Delivery.status.in_(_NON_TERMINAL))
    )


def _end(db: Session, run: SimulationRun, status: str, reason: str) -> None:
    run.status = status
    run.completed_at = db.scalar(select(func.now()))
    run.end_reason = reason
    db.flush()
    run.summary = compute_summary(db, run)


def finalize_if_done(db: Session, run_id: uuid.UUID) -> bool:
    """Called after a simulation delivery reaches a terminal state. Safe to call concurrently."""
    if _remaining(db, run_id):
        return False
    run = db.scalar(
        select(SimulationRun)
        .where(SimulationRun.id == run_id, SimulationRun.status == "running")
        .with_for_update(skip_locked=True)
    )
    if run is None or _remaining(db, run_id):
        db.rollback()
        return False
    _end(db, run, "completed", "all deliveries reached a final state")
    db.commit()
    return True


def _stop_deliveries(db: Session, run_id: uuid.UUID, reason: str) -> int:
    params = {"id": run_id, "reason": reason}
    db.execute(
        text(
            "INSERT INTO delivery_transitions (workspace_id, delivery_id, from_status, to_status, reason, created_at) "
            "SELECT workspace_id, id, status, 'cancelled', :reason, now() FROM deliveries "
            "WHERE simulation_run_id = :id AND status IN ('pending', 'processing', 'retry_scheduled')"
        ),
        params,
    )
    n = db.execute(
        text(
            "UPDATE deliveries SET status = 'cancelled', completed_at = now(), next_attempt_at = NULL, updated_at = now() "
            "WHERE simulation_run_id = :id AND status IN ('pending', 'processing', 'retry_scheduled')"
        ),
        params,
    ).rowcount
    db.execute(
        text(
            "UPDATE jobs SET status = 'succeeded', completed_at = now(), last_error = :reason, updated_at = now() "
            "WHERE status = 'queued' AND job_type = 'deliver' AND payload->>'delivery_id' IN "
            "(SELECT id::text FROM deliveries WHERE simulation_run_id = :id)"
        ),
        params,
    )
    return n


def get_run(db: Session, ws: Workspace, run_id: uuid.UUID) -> SimulationRun:
    run = db.scalar(select(SimulationRun).where(SimulationRun.id == run_id, SimulationRun.workspace_id == ws.id))
    if run is None:
        raise not_found("Simulation")
    return run


def cancel_run(db: Session, ws: Workspace, actor: str, run_id: uuid.UUID) -> SimulationRun:
    run = db.scalar(
        select(SimulationRun).where(SimulationRun.id == run_id, SimulationRun.workspace_id == ws.id).with_for_update()
    )
    if run is None:
        raise not_found("Simulation")
    if run.status != "running":
        raise ServiceError(409, "simulation_finished", f"This simulation already ended ({run.status}).")
    stopped = _stop_deliveries(db, run.id, "simulation cancelled")
    _end(db, run, "cancelled", f"cancelled by user; {stopped} unfinished deliveries stopped")
    audit(db, ws.id, actor, "simulation.cancelled", "simulation_run", run.id, stopped=stopped)
    db.commit()
    return run


def enforce_deadline(db: Session, payload: dict) -> str:
    """Worker job scheduled at the run's deadline."""
    run = db.scalar(select(SimulationRun).where(SimulationRun.id == uuid.UUID(payload["run_id"])).with_for_update())
    if run is None or run.status != "running":
        db.rollback()
        return "already ended"
    if _remaining(db, run.id) == 0:
        _end(db, run, "completed", "all deliveries reached a final state")
        db.commit()
        return "completed"
    stopped = _stop_deliveries(db, run.id, "simulation exceeded its maximum runtime")
    limit = run.configuration.get("max_runtime_s")
    _end(db, run, "timed_out", f"exceeded the {limit}s runtime limit; {stopped} unfinished deliveries stopped")
    audit(db, run.workspace_id, "system", "simulation.timed_out", "simulation_run", run.id, stopped=stopped)
    db.commit()
    return "timed_out"


def receiver_behavior_for_run(db: Session, workspace_id: uuid.UUID, run_id: str) -> dict | None:
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        return None
    run = db.scalar(select(SimulationRun).where(SimulationRun.id == rid, SimulationRun.workspace_id == workspace_id))
    if run is None:
        return None
    return {**run.configuration["receiver"], "seed": run.random_seed}


def list_runs(db: Session, ws: Workspace, limit: int = 50) -> list[SimulationRun]:
    return list(
        db.scalars(
            select(SimulationRun)
            .where(SimulationRun.workspace_id == ws.id)
            .order_by(SimulationRun.created_at.desc())
            .limit(limit)
        )
    )


def summary_for(db: Session, run: SimulationRun) -> dict:
    # Running runs are measured live; ended runs use the stored snapshot unless late attempts landed after it.
    if run.status == "running" or run.summary is None:
        return compute_summary(db, run)
    live = compute_summary(db, run)
    return live if live["attempts"] != run.summary.get("attempts") else run.summary


def flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, f"{key}."))
        else:
            out[key] = v
    return out


def compare_runs(db: Session, ws: Workspace, run_ids: list[uuid.UUID]) -> dict:
    runs = [get_run(db, ws, i) for i in dict.fromkeys(run_ids)]
    configs = [flatten({k: v for k, v in r.configuration.items() if k != "endpoint_ids"}) for r in runs]
    for c, r in zip(configs, runs):
        c["endpoint_count"] = len(r.configuration["endpoint_ids"])
    keys = sorted(set().union(*configs))
    differences = [
        {"key": k, "values": [c.get(k) for c in configs]} for k in keys if len({repr(c.get(k)) for c in configs}) > 1
    ]
    return {"runs": [(r, summary_for(db, r)) for r in runs], "config_differences": differences}
