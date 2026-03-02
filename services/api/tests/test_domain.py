import random

import pytest
from pydantic import ValidationError

from app.core.security import sign_payload, verify_signature
from app.domain.receiver import ReceiverBehavior, decide
from app.domain.retry import RetryPolicySpec, base_delay_ms, is_retryable, retry_delay_ms


def test_backoff_grows_and_caps():
    p = RetryPolicySpec(initial_delay_ms=100, multiplier=2, max_delay_ms=500, jitter="none")
    assert [base_delay_ms(p, n) for n in range(1, 6)] == [100, 200, 400, 500, 500]


@pytest.mark.parametrize("jitter,lo,hi", [("full", 0, 400), ("equal", 200, 400), ("none", 400, 400)])
def test_jitter_bounds(jitter, lo, hi):
    p = RetryPolicySpec(initial_delay_ms=100, multiplier=2, max_delay_ms=1000, jitter=jitter)
    rng = random.Random(7)
    for _ in range(500):
        assert lo <= retry_delay_ms(p, 3, rng) <= hi


def test_policy_validation():
    with pytest.raises(ValidationError):
        RetryPolicySpec(max_attempts=100)
    with pytest.raises(ValidationError):
        RetryPolicySpec(initial_delay_ms=5000, max_delay_ms=1000)
    with pytest.raises(ValidationError):
        RetryPolicySpec(retry_on_status=[700])


def test_retryable_classification():
    p = RetryPolicySpec()
    assert is_retryable(p, 503, None) and is_retryable(p, 429, None)
    assert not is_retryable(p, 400, None)
    assert is_retryable(p, None, "timeout")
    assert not is_retryable(RetryPolicySpec(retry_on_network_errors=False), None, "timeout")


def test_seeded_decisions_reproducible():
    b = ReceiverBehavior(mode="random", failure_rate=0.5, seed=42, latency_ms_min=0, latency_ms_max=100)
    run1 = [decide(b, f"evt{i}", a) for i in range(50) for a in (1, 2)]
    run2 = [decide(b, f"evt{i}", a) for i in range(50) for a in (1, 2)]
    assert run1 == run2
    other = [decide(b.model_copy(update={"seed": 43}), f"evt{i}", a) for i in range(50) for a in (1, 2)]
    assert run1 != other
    fails = sum(d.status != 200 for d in run1)
    assert 25 < fails < 75


def test_signature_roundtrip():
    sig = sign_payload("s3cret", b"body")
    assert verify_signature("s3cret", b"body", sig)
    assert not verify_signature("s3cret", b"tampered", sig)
    assert not verify_signature("other", b"body", sig)
    assert not verify_signature("s3cret", b"body", "garbage")
