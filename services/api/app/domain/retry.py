"""Retry policy validation, presets, and backoff schedule (spec §3.5)."""

import random
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DEFAULT_RETRY_STATUSES = [408, 425, 429, 500, 502, 503, 504]


class RetryPolicySpec(BaseModel):
    max_attempts: int = Field(5, ge=1, le=15)
    initial_delay_ms: int = Field(1000, ge=100, le=600_000)
    multiplier: float = Field(2.0, ge=1.0, le=10.0)
    max_delay_ms: int = Field(30_000, ge=100, le=3_600_000)
    jitter: Literal["none", "full", "equal"] = "full"
    retry_on_status: list[int] = Field(default_factory=lambda: list(DEFAULT_RETRY_STATUSES), max_length=50)
    retry_on_network_errors: bool = True
    # Total time the worker waits for a response before recording a timeout.
    timeout_ms: int = Field(5_000, ge=200, le=10_000)
    # Honor a Retry-After header on 429/503 (bounded by max_delay_ms).
    respect_retry_after: bool = True

    @model_validator(mode="after")
    def _check(self) -> "RetryPolicySpec":
        if self.max_delay_ms < self.initial_delay_ms:
            raise ValueError("max_delay_ms must be >= initial_delay_ms")
        if any(s < 100 or s > 599 for s in self.retry_on_status):
            raise ValueError("retry_on_status must contain HTTP status codes (100-599)")
        self.retry_on_status = sorted(set(self.retry_on_status))
        return self


PRESETS: dict[str, RetryPolicySpec] = {
    "fast": RetryPolicySpec(max_attempts=4, initial_delay_ms=500, multiplier=2, max_delay_ms=4_000, jitter="full"),
    "balanced": RetryPolicySpec(
        max_attempts=6, initial_delay_ms=2_000, multiplier=2, max_delay_ms=60_000, jitter="full"
    ),
    "conservative": RetryPolicySpec(
        max_attempts=8, initial_delay_ms=10_000, multiplier=3, max_delay_ms=3_600_000, jitter="equal"
    ),
}
DEFAULT_POLICY = PRESETS["fast"]


def base_delay_ms(policy: RetryPolicySpec, failed_attempt: int) -> int:
    """Un-jittered delay before the retry that follows `failed_attempt` (1-based)."""
    raw = policy.initial_delay_ms * (policy.multiplier ** (failed_attempt - 1))
    return int(min(policy.max_delay_ms, raw))


def retry_delay_ms(policy: RetryPolicySpec, failed_attempt: int, rng: random.Random | None = None) -> int:
    rng = rng or random.Random()
    base = base_delay_ms(policy, failed_attempt)
    if policy.jitter == "full":
        return int(rng.uniform(0, base))
    if policy.jitter == "equal":
        return int(base / 2 + rng.uniform(0, base / 2))
    return base


def delay_after_failure(
    policy: RetryPolicySpec, failed_attempt: int, retry_after_s: float | None, rng: random.Random | None = None
) -> int:
    delay = retry_delay_ms(policy, failed_attempt, rng)
    if policy.respect_retry_after and retry_after_s is not None:
        delay = max(delay, min(int(retry_after_s * 1000), policy.max_delay_ms))
    return delay


def is_retryable(policy: RetryPolicySpec, status: int | None, error_category: str | None) -> bool:
    if status is not None:
        return status in policy.retry_on_status
    return policy.retry_on_network_errors and error_category is not None
