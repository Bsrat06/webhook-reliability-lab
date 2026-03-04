"""Built-in simulated receiver behavior with seeded, reproducible decisions."""

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator

FailureKind = Literal["server_error", "rate_limited", "timeout", "connection_drop"]
FAILURE_KINDS: tuple[str, ...] = ("server_error", "rate_limited", "timeout", "connection_drop")

# A "timeout" decision makes the receiver hang longer than the largest allowed delivery timeout.
TIMEOUT_HANG_MS = 11_000


class FailureMix(BaseModel):
    """Relative weights used to pick a failure category in random mode."""

    server_error: float = Field(1.0, ge=0, le=100)
    rate_limited: float = Field(0.0, ge=0, le=100)
    timeout: float = Field(0.0, ge=0, le=100)
    connection_drop: float = Field(0.0, ge=0, le=100)

    @model_validator(mode="after")
    def _check(self) -> "FailureMix":
        if sum(self.model_dump().values()) <= 0:
            raise ValueError("at least one failure category needs a positive weight")
        return self


class ReceiverBehavior(BaseModel):
    mode: Literal["succeed", "fail", "fail_first_n", "random"] = "succeed"
    # How "fail" and "fail_first_n" fail.
    failure_kind: FailureKind = "server_error"
    failure_status: int = Field(503, ge=400, le=599)
    fail_first_n: int = Field(2, ge=0, le=15)
    # Random mode: probability that an attempt fails, and which way it fails.
    failure_rate: float = Field(0.3, ge=0.0, le=1.0)
    failure_mix: FailureMix = Field(default_factory=FailureMix)
    retry_after_s: int = Field(1, ge=0, le=30)
    # Probability that a request the receiver processed loses its response (connection dropped after
    # processing). The sender sees a failure and retries, so the receiver gets a duplicate.
    ack_loss_rate: float = Field(0.0, ge=0.0, le=1.0)
    # Deduplicate on the Idempotency-Key header (falling back to the event ID).
    idempotency: bool = True
    latency_ms_min: int = Field(20, ge=0, le=5_000)
    latency_ms_max: int = Field(120, ge=0, le=5_000)
    seed: int = Field(1, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def _check(self) -> "ReceiverBehavior":
        if self.latency_ms_max < self.latency_ms_min:
            raise ValueError("latency_ms_max must be >= latency_ms_min")
        if self.mode != "succeed" and self.failure_kind == "server_error" and self.failure_status == 429:
            self.failure_kind = "rate_limited"
        return self


@dataclass(frozen=True)
class Decision:
    """What the receiver does with one attempt. `kind` is None on success."""

    kind: str | None
    status: int
    latency_ms: int
    ack_lost: bool = False


def unit(seed: int, *parts: object) -> float:
    """Deterministic uniform value in [0, 1) derived from the seed and parts."""
    digest = hashlib.sha256(":".join(str(p) for p in (seed, *parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _pick_kind(behavior: ReceiverBehavior, key: str, attempt: int) -> str:
    weights = behavior.failure_mix.model_dump()
    total = sum(weights.values())
    x = unit(behavior.seed, key, attempt, "kind") * total
    for kind in FAILURE_KINDS:
        x -= weights[kind]
        if x < 0:
            return kind
    return next(k for k in reversed(FAILURE_KINDS) if weights[k] > 0)


def _status_for(behavior: ReceiverBehavior, kind: str) -> int:
    if kind == "rate_limited":
        return 429
    if kind == "server_error":
        return behavior.failure_status if behavior.failure_status != 429 else 503
    return 0  # timeout / connection_drop never produce a status


def decide(behavior: ReceiverBehavior, key: str, attempt: int) -> Decision:
    """Pure function of (behavior, key, attempt): the same inputs always give the same decision."""
    if behavior.mode == "succeed":
        fail = False
    elif behavior.mode == "fail":
        fail = True
    elif behavior.mode == "fail_first_n":
        fail = attempt <= behavior.fail_first_n
    else:
        fail = unit(behavior.seed, key, attempt, "fail") < behavior.failure_rate
    span = behavior.latency_ms_max - behavior.latency_ms_min
    latency = behavior.latency_ms_min + int(unit(behavior.seed, key, attempt, "latency") * (span + 1))
    if not fail:
        lost = behavior.ack_loss_rate > 0 and unit(behavior.seed, key, attempt, "ack") < behavior.ack_loss_rate
        return Decision(kind=None, status=200, latency_ms=latency, ack_lost=lost)
    kind = _pick_kind(behavior, key, attempt) if behavior.mode == "random" else behavior.failure_kind
    if behavior.mode != "random" and kind == "server_error" and behavior.failure_status < 500:
        kind = "client_error"
    status = behavior.failure_status if kind == "client_error" else _status_for(behavior, kind)
    return Decision(kind=kind, status=status, latency_ms=TIMEOUT_HANG_MS if kind == "timeout" else latency)
