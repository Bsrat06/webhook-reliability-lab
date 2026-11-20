"""Workspace analytics, computed on demand from persisted deliveries and attempts. No cached or sample values."""

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Workspace

WINDOWS = {
    "1h": (timedelta(hours=1), "1 minute"),
    "24h": (timedelta(hours=24), "30 minutes"),
    "7d": (timedelta(days=7), "6 hours"),
}
MIN_PERCENTILE_SAMPLES = 20

DEFINITIONS = {
    "window": "Deliveries are counted by creation time, attempts by when they were made, within the selected window.",
    "success_rate": "succeeded ÷ (succeeded + dead-lettered). In-flight and cancelled deliveries are excluded.",
    "dead_letter_rate": "dead-lettered ÷ (succeeded + dead-lettered).",
    "retry_rate": "deliveries with more than one attempt ÷ deliveries with at least one attempt.",
    "latency": "duration of individual HTTP attempts. Percentiles need at least 20 attempts.",
}


def _filters(source: str) -> str:
    if source == "manual":
        return "AND d.simulation_run_id IS NULL"
    if source == "simulation":
        return "AND d.simulation_run_id IS NOT NULL"
    return ""


def overview(db: Session, ws: Workspace, window: str, source: str) -> dict:
    span, bucket = WINDOWS.get(window, (None, "1 day"))
    params = {"ws": ws.id, "since": None}
    if span is not None:
        params["since"] = db.scalar(text("SELECT now()")) - span
    since_d = "AND d.created_at >= :since" if span else ""
    since_a = "AND a.created_at >= :since" if span else ""
    src = _filters(source)

    by_status = dict(
        db.execute(
            text(f"SELECT d.status, count(*) FROM deliveries d WHERE d.workspace_id = :ws {since_d} {src} GROUP BY 1"),
            params,
        ).all()
    )
    retry = db.execute(
        text(
            "SELECT count(*) FILTER (WHERE d.attempt_count > 1), count(*) FILTER (WHERE d.attempt_count > 0) "
            f"FROM deliveries d WHERE d.workspace_id = :ws {since_d} {src}"
        ),
        params,
    ).one()
    attempts_from = (
        f"FROM delivery_attempts a JOIN deliveries d ON d.id = a.delivery_id WHERE a.workspace_id = :ws {since_a} {src}"
    )
    lat = db.execute(
        text(
            f"SELECT count(*), percentile_cont(ARRAY[0.5, 0.95, 0.99]) WITHIN GROUP (ORDER BY a.duration_ms) {attempts_from}"
        ),
        params,
    ).one()
    categories = dict(
        db.execute(
            text(f"SELECT coalesce(a.error_category, 'success'), count(*) {attempts_from} GROUP BY 1"), params
        ).all()
    )
    volume = [
        {"bucket": b.isoformat(), "success": s, "failure": f}
        for b, s, f in db.execute(
            text(
                f"SELECT date_bin(interval '{bucket}', a.created_at, timestamptz '2000-01-01') AS b, "
                "count(*) FILTER (WHERE a.outcome = 'success'), count(*) FILTER (WHERE a.outcome <> 'success') "
                f"{attempts_from} GROUP BY 1 ORDER BY 1"
            ),
            params,
        ).all()
    ]
    per_endpoint = {
        i: (t, f)
        for i, t, f in db.execute(
            text(
                f"SELECT d.endpoint_id, count(*), count(*) FILTER (WHERE a.outcome <> 'success') {attempts_from} GROUP BY 1"
            ),
            params,
        ).all()
    }
    dead_per_endpoint = dict(
        db.execute(
            text(
                "SELECT d.endpoint_id, count(*) FROM deliveries d WHERE d.workspace_id = :ws "
                f"AND d.status = 'dead_lettered' {since_d} {src} GROUP BY 1"
            ),
            params,
        ).all()
    )
    endpoints = [
        {
            "endpoint_id": str(i),
            "name": name,
            "attempts": per_endpoint.get(i, (0, 0))[0],
            "failed_attempts": per_endpoint.get(i, (0, 0))[1],
            "dead_lettered": dead_per_endpoint.get(i, 0),
        }
        for i, name in db.execute(
            text("SELECT id, name FROM endpoints WHERE workspace_id = :ws ORDER BY name"), params
        ).all()
    ]
    distribution = {
        str(k): v
        for k, v in db.execute(
            text(
                "SELECT d.attempt_count, count(*) FROM deliveries d WHERE d.workspace_id = :ws "
                f"AND d.status IN ('succeeded', 'dead_lettered') {since_d} {src} GROUP BY 1 ORDER BY 1"
            ),
            params,
        ).all()
    }
    succeeded, dead = by_status.get("succeeded", 0), by_status.get("dead_lettered", 0)
    decided = succeeded + dead
    n = lat[0]
    p = lat[1] if n >= MIN_PERCENTILE_SAMPLES and lat[1] else None
    return {
        "window": window if span else "all",
        "source": source,
        "bucket": bucket,
        "since": params["since"].isoformat() if params["since"] else None,
        "definitions": DEFINITIONS,
        "deliveries": {"total": sum(by_status.values()), "by_status": by_status},
        "attempts": n,
        "success_rate": round(succeeded / decided, 4) if decided else None,
        "dead_letter_rate": round(dead / decided, 4) if decided else None,
        "retry_rate": round(retry[0] / retry[1], 4) if retry[1] else None,
        "latency_ms": {
            "n": n,
            "p50": round(p[0]) if p else None,
            "p95": round(p[1]) if p else None,
            "p99": round(p[2]) if p else None,
            "insufficient": p is None,
        },
        "failure_categories": categories,
        "volume": volume,
        "endpoints": endpoints,
        "attempt_distribution": distribution,
    }
