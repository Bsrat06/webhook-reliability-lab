"""Relational schema (spec §6). PostgreSQL is the source of truth for all state."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _ws_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


DELIVERY_STATUSES = ("pending", "processing", "retry_scheduled", "succeeded", "dead_lettered", "cancelled")
TERMINAL_DELIVERY_STATUSES = ("succeeded", "dead_lettered", "cancelled")
JOB_STATUSES = ("queued", "running", "succeeded", "failed")
SIMULATION_STATUSES = ("running", "completed", "cancelled", "timed_out")
ACTIVE_SIMULATION_STATUSES = ("running",)


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[uuid.UUID] = _uuid()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    workspace_type: Mapped[str] = mapped_column(String(16), nullable=False, default="guest")
    created_at: Mapped[datetime] = _created()
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(_in("workspace_type", ("guest", "account")), name="ck_workspace_type"),
        Index("ix_workspaces_expires_at", "expires_at"),
    )


class GuestSession(Base):
    __tablename__ = "guest_sessions"
    id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Endpoint(Base):
    __tablename__ = "endpoints"
    id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    endpoint_type: Mapped[str] = mapped_column(String(16), nullable=False)
    destination_url: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    secret_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    # Validated by app.schemas.endpoints.ReceiverBehavior / RetryPolicySpec.
    receiver_config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    retry_policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    __table_args__ = (
        CheckConstraint(_in("endpoint_type", ("builtin", "external")), name="ck_endpoint_type"),
        CheckConstraint("endpoint_type = 'builtin' OR destination_url IS NOT NULL", name="ck_endpoint_url"),
        Index("ix_endpoints_ws", "workspace_id", "created_at"),
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulation_runs.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        Index("ix_events_ws_created", "workspace_id", "created_at"),
        Index("ix_events_simulation", "simulation_run_id", "created_at"),
    )


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    retry_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)  # snapshot at creation
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_category: Mapped[str | None] = mapped_column(String(40))
    replay_of_delivery_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deliveries.id", ondelete="SET NULL")
    )
    simulation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("simulation_runs.id", ondelete="CASCADE")
    )
    # Seeded decision key sent to the built-in receiver for simulation deliveries.
    simulation_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    __table_args__ = (
        CheckConstraint(_in("status", DELIVERY_STATUSES), name="ck_delivery_status"),
        CheckConstraint("attempt_count >= 0", name="ck_delivery_attempts"),
        Index("ix_deliveries_ws_status_created", "workspace_id", "status", "created_at"),
        Index("ix_deliveries_event", "event_id"),
        Index("ix_deliveries_endpoint", "endpoint_id"),
        Index("ix_deliveries_replay_of", "replay_of_delivery_id"),
        Index("ix_deliveries_simulation_status", "simulation_run_id", "status"),
    )


class DeliveryAttempt(Base):
    """Immutable record of one HTTP delivery attempt."""

    __tablename__ = "delivery_attempts"
    id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    request_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    request_headers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_headers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    response_body_excerpt: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    worker_id: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        UniqueConstraint("delivery_id", "attempt_number", name="uq_attempt_number"),
        CheckConstraint(
            _in("outcome", ("success", "retryable_failure", "permanent_failure")), name="ck_attempt_outcome"
        ),
    )


class DeliveryTransition(Base):
    """Auditable state-transition history for a delivery."""

    __tablename__ = "delivery_transitions"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_transitions_delivery", "delivery_id", "id"),)


class DeadLetter(Base):
    __tablename__ = "dead_letters"
    id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    delivery_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_dead_letters_ws", "workspace_id", "created_at"),)


class Job(Base):
    """Durable job; claimed with FOR UPDATE SKIP LOCKED and protected by an expiring lease."""

    __tablename__ = "jobs"
    id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE")
    )
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(80))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Jobs sharing a concurrency key never run more than concurrency_limit at once (per-workspace limits).
    concurrency_key: Mapped[str | None] = mapped_column(String(80))
    concurrency_limit: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="ck_job_status"),
        CheckConstraint("concurrency_limit IS NULL OR concurrency_limit > 0", name="ck_job_concurrency"),
        Index("ix_jobs_due", "status", "scheduled_at"),
        Index("ix_jobs_lease", "status", "lease_expires_at"),
        Index("ix_jobs_running_group", "concurrency_key", postgresql_where=text("status = 'running'")),
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    started_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IdempotencyRecord(Base):
    """Keys processed by a built-in receiver. Uniqueness is enforced by the database, not application checks."""

    __tablename__ = "idempotency_records"
    id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    endpoint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("endpoints.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    first_delivery_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    first_seen_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    processing_result: Mapped[str] = mapped_column(String(20), nullable=False, default="processed")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        UniqueConstraint("endpoint_id", "idempotency_key", name="uq_idempotency_scope"),
        CheckConstraint("seen_count >= 1", name="ck_idempotency_seen"),
        Index("ix_idempotency_ws", "workspace_id", "last_seen_at"),
        Index("ix_idempotency_expires", "expires_at"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    actor: Mapped[str] = mapped_column(String(80), nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(64))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_audit_ws", "workspace_id", "id"),)


class SimulationRun(Base):
    __tablename__ = "simulation_runs"
    id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    preset: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # Validated by app.domain.simulation.SimulationConfig.
    configuration: Mapped[dict] = mapped_column(JSONB, nullable=False)
    random_seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_count: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(Text)
    # Snapshot computed from persisted deliveries and attempts when the run ends.
    summary: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
    __table_args__ = (
        CheckConstraint(_in("status", SIMULATION_STATUSES), name="ck_simulation_status"),
        CheckConstraint("event_count > 0 AND delivery_count > 0", name="ck_simulation_counts"),
        Index("ix_simulations_ws", "workspace_id", "created_at"),
        Index("ix_simulations_status", "status"),
    )
