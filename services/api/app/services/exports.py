"""Bounded CSV/JSON exports of workspace data. Secrets and request headers are never exported."""

import csv
import io
import json
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Workspace
from app.services import analytics

# dataset -> (SQL selecting the columns, whether a simulation filter applies). All queries are workspace-scoped.
DATASETS: dict[str, tuple[str, bool]] = {
    "events": (
        "SELECT e.id, e.event_type, e.source, e.simulation_run_id, e.idempotency_key, e.correlation_id, e.created_at, "
        "e.payload FROM events e WHERE e.workspace_id = :ws {sim} ORDER BY e.created_at, e.id",
        True,
    ),
    "deliveries": (
        "SELECT d.id, d.event_id, d.endpoint_id, ep.name AS endpoint_name, d.status, d.attempt_count, "
        "d.replay_of_delivery_id, d.simulation_run_id, d.last_error_category, d.created_at, d.completed_at "
        "FROM deliveries d JOIN endpoints ep ON ep.id = d.endpoint_id WHERE d.workspace_id = :ws {sim} "
        "ORDER BY d.created_at, d.id",
        True,
    ),
    "attempts": (
        "SELECT a.id, a.delivery_id, d.event_id, a.attempt_number, a.request_started_at, a.duration_ms, "
        "a.response_status, a.error_category, a.outcome, a.response_headers->>'x-receiver-result' AS receiver_result, "
        "a.worker_id FROM delivery_attempts a JOIN deliveries d ON d.id = a.delivery_id "
        "WHERE a.workspace_id = :ws {sim} ORDER BY a.request_started_at, a.id",
        True,
    ),
    "dead_letters": (
        "SELECT dl.id, dl.delivery_id, d.event_id, e.event_type, ep.name AS endpoint_name, dl.reason, d.attempt_count, "
        "dl.created_at, dl.resolved_at, dl.resolution_note FROM dead_letters dl "
        "JOIN deliveries d ON d.id = dl.delivery_id JOIN events e ON e.id = d.event_id "
        "JOIN endpoints ep ON ep.id = d.endpoint_id WHERE dl.workspace_id = :ws {sim} ORDER BY dl.created_at, dl.id",
        True,
    ),
    "simulations": (
        "SELECT s.id, s.name, s.preset, s.status, s.random_seed, s.event_count, s.delivery_count, s.started_at, "
        "s.completed_at, s.end_reason, s.configuration, s.summary FROM simulation_runs s WHERE s.workspace_id = :ws "
        "{sim} ORDER BY s.created_at, s.id",
        True,
    ),
}
SIM_COLUMN = {"events": "e", "deliveries": "d", "attempts": "d", "dead_letters": "d", "simulations": "s"}
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _plain(v):
    if isinstance(v, (datetime,)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


def _csv_cell(v) -> str:
    v = _plain(v)
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        v = json.dumps(v, separators=(",", ":"))
    s = str(v)
    # Neutralize spreadsheet formula injection for user-controlled text.
    return "'" + s if s.startswith(FORMULA_PREFIXES) and not _is_number(s) else s


def _is_number(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def rows(db: Session, ws: Workspace, dataset: str, simulation_run_id: uuid.UUID | None) -> tuple[list[str], list[tuple], bool]:
    limit = get_settings().quota_export_rows
    sql, _ = DATASETS[dataset]
    alias = SIM_COLUMN[dataset]
    sim = ""
    params: dict = {"ws": ws.id, "limit": limit + 1}
    if simulation_run_id:
        sim = f"AND {alias}.{'id' if dataset == 'simulations' else 'simulation_run_id'} = :sim"
        params["sim"] = simulation_run_id
    result = db.execute(text(sql.format(sim=sim) + " LIMIT :limit"), params)
    columns = list(result.keys())
    data = [tuple(r) for r in result.all()]
    truncated = len(data) > limit
    return columns, data[:limit], truncated


def render(db: Session, ws: Workspace, dataset: str, fmt: str, simulation_run_id: uuid.UUID | None) -> tuple[str, bool, int]:
    """Returns (body, truncated, row_count)."""
    if dataset == "analytics":
        data = analytics.overview(db, ws, "all", "all")
        if fmt == "json":
            return json.dumps(data, indent=2), False, 1
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["metric", "value"])
        for k, v in _flatten(data):
            w.writerow([_csv_cell(k), _csv_cell(v)])
        return buf.getvalue(), False, 1
    columns, data, truncated = rows(db, ws, dataset, simulation_run_id)
    if fmt == "json":
        body = {
            "dataset": dataset,
            "exported_at": datetime.now().astimezone().isoformat(),
            "row_count": len(data),
            "truncated": truncated,
            "rows": [{c: _plain(v) for c, v in zip(columns, r)} for r in data],
        }
        return json.dumps(body, default=str), truncated, len(data)
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(columns)
    for r in data:
        w.writerow([_csv_cell(v) for v in r])
    return buf.getvalue(), truncated, len(data)


def _flatten(d: dict, prefix: str = ""):
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            yield from _flatten(v, f"{key}.")
        elif isinstance(v, list):
            yield key, json.dumps(v, separators=(",", ":"))
        else:
            yield key, v
