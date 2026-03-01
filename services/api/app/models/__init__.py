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
    created_at: Mapped[datetime] = _created()
    __table_args__ = (Index("ix_events_ws_created", "workspace_id", "created_at"),)


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[uuid.UUID] = _uuid()
    workspace_id: Mapped[uuid.UUID] = _ws_fk()
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False)
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
        CheckConstraint(_in("outcome", ("success", "retryable_failure", "permanent_failure")), name="ck_attempt_outcome"),
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
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="ck_job_status"),
        Index("ix_jobs_due", "status", "scheduled_at"),
        Index("ix_jobs_lease", "status", "lease_expires_at"),
    )


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    started_at: Mapped[datetime] = _created()
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
