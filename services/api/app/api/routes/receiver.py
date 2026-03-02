"""Built-in simulated receiver. Called by the worker over real HTTP; authenticated by HMAC signature."""

import asyncio
import uuid

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from app.core.security import decrypt_secret, verify_signature
from app.db.session import SessionLocal
from app.domain.receiver import ReceiverBehavior, decide
from app.models import Endpoint

router = APIRouter(tags=["receiver"], include_in_schema=False)
MAX_BODY = 512 * 1024


def _load(endpoint_id: uuid.UUID) -> tuple[str, dict] | None:
    with SessionLocal() as db:
        ep = db.get(Endpoint, endpoint_id)
        if ep is None or ep.endpoint_type != "builtin":
            return None
        return decrypt_secret(ep.secret_ciphertext), ep.receiver_config


@router.post("/receiver/{endpoint_id}")
async def receive(endpoint_id: uuid.UUID, request: Request) -> JSONResponse:
    body = await request.body()
    if len(body) > MAX_BODY:
        return JSONResponse({"error": "payload too large"}, status_code=413)
    loaded = await run_in_threadpool(_load, endpoint_id)
    if loaded is None:
        return JSONResponse({"error": "unknown receiver"}, status_code=404)
    secret, config = loaded
    if not verify_signature(secret, body, request.headers.get("x-webhook-signature")):
        return JSONResponse({"error": "invalid signature"}, status_code=401)
    behavior = ReceiverBehavior.model_validate(config)
    try:
        attempt = int(request.headers.get("x-webhook-attempt", "1"))
    except ValueError:
        attempt = 1
    key = request.headers.get("x-webhook-event-id", "")
    decision = decide(behavior, key, attempt)
    await asyncio.sleep(decision.latency_ms / 1000)
    ok = decision.status < 300
    return JSONResponse(
        {"received": ok, "attempt": attempt, "mode": behavior.mode},
        status_code=decision.status,
        headers={"x-receiver-decision": f"mode={behavior.mode};latency_ms={decision.latency_ms}"},
    )
