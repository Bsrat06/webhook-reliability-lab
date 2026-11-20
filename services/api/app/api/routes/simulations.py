import asyncio
import json
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.api.deps import DB, Actor, CurrentWorkspace
from app.api.routes.events import DeliveryFilter, event_page, parse_cursor
from app.db.session import SessionLocal
from app.domain.simulation import PRESETS, SimulationConfig
from app.models import SimulationRun
from app.schemas import CompareIn, CompareOut, EventList, PresetOut, SimulationIn, SimulationOut
from app.services import simulations as svc

router = APIRouter(prefix="/api/v1/simulations", tags=["simulations"])
STREAM_MAX_SECONDS = 15 * 60


def _out(db, run: SimulationRun) -> SimulationOut:
    return SimulationOut.model_validate(run).model_copy(update={"summary": svc.summary_for(db, run)})


@router.get("/presets", response_model=list[PresetOut])
def list_presets():
    out = []
    for key, (title, description, partial) in PRESETS.items():
        # Show every field the preset executes, with defaults filled in. Endpoints and seed are chosen per run.
        cfg = SimulationConfig(endpoint_ids=[uuid.UUID(int=0)], **partial).model_dump(
            mode="json", exclude={"endpoint_ids"}
        )
        out.append(PresetOut(key=key, title=title, description=description, config=cfg))
    return out


@router.get("", response_model=list[SimulationOut])
def list_simulations(ws: CurrentWorkspace, db: DB, limit: int = Query(50, ge=1, le=100)):
    return [_out(db, r) for r in svc.list_runs(db, ws, limit)]


@router.post("", response_model=SimulationOut, status_code=201)
def create_simulation(body: SimulationIn, ws: CurrentWorkspace, actor: Actor, db: DB):
    run = svc.create_run(db, ws, actor, body.config, body.name, body.preset)
    return _out(db, run)


@router.post("/compare", response_model=CompareOut)
def compare(body: CompareIn, ws: CurrentWorkspace, db: DB):
    result = svc.compare_runs(db, ws, body.run_ids)
    return CompareOut(
        runs=[SimulationOut.model_validate(r).model_copy(update={"summary": s}) for r, s in result["runs"]],
        config_differences=result["config_differences"],
    )


@router.get("/{run_id}", response_model=SimulationOut)
def get_simulation(run_id: uuid.UUID, ws: CurrentWorkspace, db: DB):
    return _out(db, svc.get_run(db, ws, run_id))


@router.get("/{run_id}/summary")
def get_summary(run_id: uuid.UUID, ws: CurrentWorkspace, db: DB) -> dict:
    return svc.summary_for(db, svc.get_run(db, ws, run_id))


@router.post("/{run_id}/cancel", response_model=SimulationOut)
def cancel_simulation(run_id: uuid.UUID, ws: CurrentWorkspace, actor: Actor, db: DB):
    return _out(db, svc.cancel_run(db, ws, actor, run_id))


@router.get("/{run_id}/events", response_model=EventList)
def simulation_events(
    run_id: uuid.UUID,
    ws: CurrentWorkspace,
    db: DB,
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = Query(None, max_length=100),
    delivery_status: DeliveryFilter | None = None,
):
    svc.get_run(db, ws, run_id)
    return event_page(
        db, ws, limit, cursor=parse_cursor(cursor), simulation_run_id=run_id, delivery_status=delivery_status
    )


def _snapshot(workspace_id: uuid.UUID, run_id: uuid.UUID) -> dict | None:
    with SessionLocal() as db:
        run = db.get(SimulationRun, run_id)
        if run is None or run.workspace_id != workspace_id:
            return None
        return {
            "status": run.status,
            "end_reason": run.end_reason,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "summary": svc.summary_for(db, run),
        }


@router.get("/{run_id}/stream")
async def stream(
    run_id: uuid.UUID, request: Request, ws: CurrentWorkspace, db: DB, interval: Literal["fast", "normal"] = "normal"
):
    """Server-Sent Events. Messages are notifications derived from the database, which stays authoritative:
    clients reconnect or fall back to polling GET /simulations/{id} and lose nothing."""
    svc.get_run(db, ws, run_id)
    workspace_id = ws.id
    period = 0.5 if interval == "fast" else 1.0

    async def events():
        last = None
        started = time.monotonic()
        last_sent = 0.0
        yield "retry: 2000\n\n"
        while time.monotonic() - started < STREAM_MAX_SECONDS:
            if await request.is_disconnected():
                return
            snap = await run_in_threadpool(_snapshot, workspace_id, run_id)
            if snap is None:
                yield "event: gone\ndata: {}\n\n"
                return
            body = json.dumps(snap, separators=(",", ":"))
            if body != last:
                yield f"event: progress\ndata: {body}\n\n"
                last, last_sent = body, time.monotonic()
            elif time.monotonic() - last_sent > 15:
                yield ": keep-alive\n\n"
                last_sent = time.monotonic()
            if snap["status"] != "running":
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(period)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # no-transform keeps proxies (including Next's compression) from buffering the stream.
        headers={"cache-control": "no-cache, no-transform", "x-accel-buffering": "no"},
    )
