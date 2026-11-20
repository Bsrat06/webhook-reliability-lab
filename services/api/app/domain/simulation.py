"""Simulation configuration and scenario presets.

A simulation is a real run: it creates events and deliveries that the worker sends over HTTP to built-in
receivers. Presets are nothing more than concrete configurations; every field they set is executed.
Failure decisions are a pure function of (seed, simulation key, attempt), so the same seed and
configuration reproduce the same decisions. Wall-clock timing still varies with load.
"""

import hashlib
import uuid

from pydantic import BaseModel, Field, field_validator

from app.domain.receiver import FailureMix, ReceiverBehavior
from app.domain.retry import RetryPolicySpec

EXPERIMENT_POLICY = RetryPolicySpec(
    max_attempts=5, initial_delay_ms=200, multiplier=2, max_delay_ms=2_000, jitter="full", timeout_ms=2_000
)


class SimulationConfig(BaseModel):
    event_count: int = Field(100, ge=1, le=1_000)
    endpoint_ids: list[uuid.UUID] = Field(min_length=1, max_length=5)
    event_type: str = Field("sim.order.created", min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:\-]+$")
    # Events are released evenly over this window instead of all at once.
    emit_over_ms: int = Field(0, ge=0, le=60_000)
    concurrency: int = Field(2, ge=1, le=16)
    receiver: ReceiverBehavior = Field(default_factory=ReceiverBehavior)
    retry_policy: RetryPolicySpec = Field(default_factory=lambda: EXPERIMENT_POLICY.model_copy())
    use_idempotency_keys: bool = True
    # Probability that the producer sends an event twice with the same idempotency key.
    duplicate_send_rate: float = Field(0.0, ge=0.0, le=0.5)
    max_runtime_s: int = Field(300, ge=10, le=3_600)
    seed: int = Field(1, ge=0, le=2**31 - 1)

    @field_validator("endpoint_ids")
    @classmethod
    def _unique(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(v)) != len(v):
            raise ValueError("endpoint_ids must be unique")
        return v


def _receiver(**kw) -> ReceiverBehavior:
    return ReceiverBehavior(mode="random", latency_ms_min=20, latency_ms_max=120, **kw)


def _policy(**kw) -> RetryPolicySpec:
    return EXPERIMENT_POLICY.model_copy(update=kw)


# Each preset is (title, description, partial configuration). Endpoint IDs and seed come from the user.
PRESETS: dict[str, tuple[str, str, dict]] = {
    "healthy": (
        "Healthy endpoint",
        "Every attempt succeeds with low latency. A baseline for the other scenarios.",
        {"receiver": ReceiverBehavior(mode="succeed", latency_ms_min=10, latency_ms_max=60), "retry_policy": _policy()},
    ),
    "intermittent_errors": (
        "Intermittent server errors",
        "25% of attempts fail with HTTP 503. Retries with backoff recover almost all of them.",
        {"receiver": _receiver(failure_rate=0.25, failure_status=503), "retry_policy": _policy()},
    ),
    "rate_limited": (
        "Rate-limited receiver",
        "40% of attempts get HTTP 429 with Retry-After: 1. The policy honors Retry-After, so retries slow down.",
        {
            "receiver": _receiver(
                failure_rate=0.4, failure_mix=FailureMix(server_error=0, rate_limited=1), retry_after_s=1
            ),
            "retry_policy": _policy(max_delay_ms=3_000),
        },
    ),
    "slow_timeouts": (
        "Slow receiver / timeouts",
        "Responses take 200–900 ms and 20% never answer. The 1 s request timeout turns those into timeout failures.",
        {
            "receiver": ReceiverBehavior(
                mode="random",
                failure_rate=0.2,
                failure_mix=FailureMix(server_error=0, timeout=1),
                latency_ms_min=200,
                latency_ms_max=900,
            ),
            "retry_policy": _policy(timeout_ms=1_000),
        },
    ),
    "unstable_network": (
        "Unstable network",
        "30% of attempts fail at the network level: connections drop mid-response or time out.",
        {
            "receiver": _receiver(
                failure_rate=0.3, failure_mix=FailureMix(server_error=0, connection_drop=0.7, timeout=0.3)
            ),
            "retry_policy": _policy(timeout_ms=1_000),
        },
    ),
    "duplicate_challenge": (
        "Duplicate delivery challenge",
        "Deliveries succeed, but 25% of acknowledgements are lost and 10% of events are sent twice. "
        "The idempotent receiver suppresses the duplicates; the counts show how many it caught.",
        {
            "receiver": ReceiverBehavior(
                mode="succeed", ack_loss_rate=0.25, idempotency=True, latency_ms_min=10, latency_ms_max=80
            ),
            "retry_policy": _policy(),
            "duplicate_send_rate": 0.1,
        },
    ),
    "retry_stress": (
        "Retry policy stress test",
        "60% of attempts fail with HTTP 500. With 5 attempts about 8% of deliveries are expected to exhaust "
        "retries; raise max attempts and compare.",
        {"receiver": _receiver(failure_rate=0.6, failure_status=500), "retry_policy": _policy()},
    ),
    "mixed": (
        "Mixed failure environment",
        "35% of attempts fail across 5xx, 429, timeouts and dropped connections, and 5% of acknowledgements are lost.",
        {
            "receiver": _receiver(
                failure_rate=0.35,
                failure_mix=FailureMix(server_error=0.4, rate_limited=0.2, timeout=0.2, connection_drop=0.2),
                ack_loss_rate=0.05,
                retry_after_s=1,
            ),
            "retry_policy": _policy(timeout_ms=1_000),
        },
    ),
}


def preset_config(name: str, endpoint_ids: list[uuid.UUID], seed: int = 1, event_count: int = 100) -> SimulationConfig:
    _, _, partial = PRESETS[name]
    return SimulationConfig(endpoint_ids=endpoint_ids, seed=seed, event_count=event_count, **partial)


def simulation_key(seed: int, index: int, endpoint_index: int, duplicate: bool = False) -> str:
    """Stable decision key: identical across runs with the same seed, so decisions reproduce."""
    return f"{seed}:{index}.{endpoint_index}{'d' if duplicate else ''}"


def is_duplicate_send(seed: int, index: int, rate: float) -> bool:
    if rate <= 0:
        return False
    digest = hashlib.sha256(f"{seed}:{index}:dup".encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64 < rate
