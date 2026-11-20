"""Built-in simulated receiver. Called by the worker over real HTTP; authenticated by HMAC signature.

Failure modes are produced for real: HTTP error statuses, hanging past the sender's timeout, and
dropping the connection mid-response. Successful requests are recorded in idempotency_records, where
a unique constraint (not an application check) decides whether a key was already processed.
"""

import asyncio
import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, literal_column
from sqlalchemy.dialects.postgresql import insert

from app.core.config import get_settings
from app.core.security import decrypt_secret, verify_signature
from app.db.session import SessionLocal
from app.domain.receiver import Decision, ReceiverBehavior, decide
from app.models import Endpoint, IdempotencyRecord
from app.services.simulations import receiver_behavior_for_run

router = APIRouter(tags=["receiver"], include_in_schema=False)
MAX_BODY = 512 * 1024


class SimulatedConnectionDrop(Exception):
    """Raised mid-response so the server closes the socket and the sender sees a broken connection."""


class _DropFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        exc = record.exc_info[1] if record.exc_info else None
        while exc is not None:
            if isinstance(exc, SimulatedConnectionDrop):
                return False
            exc = exc.__cause__ or exc.__context__
        return True


logging.getLogger("uvicorn.error").addFilter(_DropFilter())


def _load(endpoint_id: uuid.UUID, simulation_run_id: str | None) -> tuple[str, uuid.UUID, dict] | None:
    with SessionLocal() as db:
        ep = db.get(Endpoint, endpoint_id)
        if ep is None or ep.endpoint_type != "builtin":
            return None
        config = ep.receiver_config
        if simulation_run_id:
            # Simulation deliveries use the run's stored behavior and seed (same workspace only).
            config = receiver_behavior_for_run(db, ep.workspace_id, simulation_run_id) or config
        return decrypt_secret(ep.secret_ciphertext), ep.workspace_id, config


def record_processing(endpoint_id: uuid.UUID, workspace_id: uuid.UUID, key: str, delivery_id: str | None) -> str:
    """Atomically record a processed key. Returns 'processed' for the first sighting, else 'duplicate'."""
    retention = timedelta(hours=get_settings().idempotency_retention_hours)
    try:
        first_delivery = uuid.UUID(delivery_id) if delivery_id else None
    except ValueError:
        first_delivery = None
    stmt = insert(IdempotencyRecord).values(
        workspace_id=workspace_id,
        endpoint_id=endpoint_id,
        idempotency_key=key[:200],
        first_delivery_id=first_delivery,
        seen_count=1,
        processing_result="processed",
        expires_at=func.now() + retention,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_idempotency_scope",
        set_={"seen_count": IdempotencyRecord.seen_count + 1, "last_seen_at": func.now()},
    ).returning(literal_column("(xmax = 0)").label("inserted"))
    with SessionLocal() as db:
        inserted = db.execute(stmt).scalar_one()
        db.commit()
    return "processed" if inserted else "duplicate"


def _dropped_response(decision: Decision) -> StreamingResponse:
    async def body():
        yield b'{"partial":'
        raise SimulatedConnectionDrop()

    # Content-Length promises more bytes than are ever sent; the server aborts the connection.
    return StreamingResponse(body(), status_code=200, media_type="application/json", headers={"content-length": "64"})


@router.post("/receiver/{endpoint_id}")
async def receive(endpoint_id: uuid.UUID, request: Request):
    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse({"error": "payload too large"}, status_code=413)
    loaded = await run_in_threadpool(_load, endpoint_id, request.headers.get("x-simulation-run-id"))
    if loaded is None:
        return JSONResponse({"error": "unknown receiver"}, status_code=404)
    secret, workspace_id, config = loaded
    if not verify_signature(secret, body, request.headers.get("x-webhook-signature")):
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    behavior = ReceiverBehavior.model_validate(config)
    try:
        attempt = int(request.headers.get("x-webhook-attempt", "1"))
    except ValueError:
        attempt = 1
    event_id = request.headers.get("x-webhook-event-id", "")
    key = request.headers.get("x-webhook-decision-key") or event_id
    decision = decide(behavior, key, attempt)
    await asyncio.sleep(decision.latency_ms / 1000)
    decision_header = f"mode={behavior.mode};latency_ms={decision.latency_ms}"

    if decision.kind == "timeout":
        # The sender's timeout fires long before this; nothing was processed.
        return JSONResponse({"error": "too slow"}, status_code=504, headers={"x-receiver-result": "timed_out"})
    if decision.kind == "connection_drop":
        return _dropped_response(decision)
    if decision.kind is not None:
        headers = {"x-receiver-decision": decision_header, "x-receiver-result": "rejected"}
        if decision.status == 429:
            headers["retry-after"] = str(behavior.retry_after_s)
        return JSONResponse({"received": False, "attempt": attempt, "reason": decision.kind}, decision.status, headers)

    result = "processed"
    if behavior.idempotency:
        idem_key = request.headers.get("idempotency-key") or event_id
        result = await run_in_threadpool(
            record_processing, endpoint_id, workspace_id, idem_key, request.headers.get("x-webhook-id")
        )
    if decision.ack_lost:
        # Processed, but the acknowledgement never reaches the sender: it will retry and we'll see a duplicate.
        return _dropped_response(decision)
    return JSONResponse(
        {"received": True, "attempt": attempt, "duplicate": result == "duplicate"},
        status_code=200,
        headers={"x-receiver-decision": decision_header, "x-receiver-result": result},
    )
