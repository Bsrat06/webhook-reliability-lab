"""Built-in simulated receiver behavior with seeded, reproducible decisions."""

import hashlib
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ReceiverBehavior(BaseModel):
    mode: Literal["succeed", "fail", "fail_first_n", "random"] = "succeed"
    failure_status: int = Field(503, ge=400, le=599)
    fail_first_n: int = Field(2, ge=0, le=15)
    failure_rate: float = Field(0.3, ge=0.0, le=1.0)
    latency_ms_min: int = Field(20, ge=0, le=15_000)
    latency_ms_max: int = Field(120, ge=0, le=15_000)
    seed: int = Field(1, ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def _check(self) -> "ReceiverBehavior":
        if self.latency_ms_max < self.latency_ms_min:
            raise ValueError("latency_ms_max must be >= latency_ms_min")
        return self


@dataclass(frozen=True)
class Decision:
    status: int
    latency_ms: int


def unit(seed: int, *parts: object) -> float:
    """Deterministic uniform value in [0, 1) derived from the seed and parts."""
    digest = hashlib.sha256(":".join(str(p) for p in (seed, *parts)).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def decide(behavior: ReceiverBehavior, key: str, attempt: int) -> Decision:
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
    return Decision(status=behavior.failure_status if fail else 200, latency_ms=latency)
