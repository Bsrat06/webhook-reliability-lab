import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Response

from app.api.deps import DB, Actor, CurrentWorkspace
from app.services import analytics, exports
from app.services.audit import audit

router = APIRouter(prefix="/api/v1", tags=["analytics", "exports"])

Dataset = Literal["events", "deliveries", "attempts", "dead_letters", "simulations", "analytics"]


@router.get("/analytics/overview")
def analytics_overview(
    ws: CurrentWorkspace,
    db: DB,
    window: Literal["1h", "24h", "7d", "all"] = "24h",
    source: Literal["all", "manual", "simulation"] = "all",
) -> dict:
    return analytics.overview(db, ws, window, source)


@router.get("/exports/{dataset}")
def export(
    dataset: Dataset,
    ws: CurrentWorkspace,
    actor: Actor,
    db: DB,
    format: Literal["csv", "json"] = "csv",
    simulation_run_id: uuid.UUID | None = None,
):
    """Download a bounded export. If the row limit is hit, the response says so (X-Export-Truncated)."""
    body, truncated, count = exports.render(db, ws, dataset, format, simulation_run_id)
    audit(
        db,
        ws.id,
        actor,
        "data.exported",
        "export",
        None,
        dataset=dataset,
        format=format,
        rows=count,
        truncated=truncated,
    )
    db.commit()
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    scope = f"-sim-{str(simulation_run_id)[:8]}" if simulation_run_id else ""
    return Response(
        body,
        media_type="text/csv; charset=utf-8" if format == "csv" else "application/json",
        headers={
            "content-disposition": f'attachment; filename="webhook-lab-{dataset}{scope}-{stamp}.{format}"',
            "x-export-rows": str(count),
            "x-export-truncated": "true" if truncated else "false",
            "cache-control": "no-store",
        },
    )
