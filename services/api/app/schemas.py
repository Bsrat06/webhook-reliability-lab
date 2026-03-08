import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.receiver import ReceiverBehavior
from app.domain.retry import RetryPolicySpec
from app.domain.simulation import SimulationConfig


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WorkspaceOut(ORM):
    id: uuid.UUID
    name: str
    workspace_type: str
    created_at: datetime
    expires_at: datetime | None


class SessionOut(BaseModel):
    workspace: WorkspaceOut
    retention_hours: int
    limits: dict[str, int]
    usage: dict[str, int]
    worker: dict[str, Any]


class EndpointIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=500)
    endpoint_type: Literal["builtin", "external"] = "builtin"
    destination_url: str | None = Field(None, max_length=2048)
    receiver_config: ReceiverBehavior | None = None
    retry_policy: RetryPolicySpec | None = None


class EndpointPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=500)
    enabled: bool | None = None
    receiver_config: ReceiverBehavior | None = None
    retry_policy: RetryPolicySpec | None = None


class EndpointOut(ORM):
    id: uuid.UUID
    name: str
    description: str
    endpoint_type: str
    destination_url: str | None
    enabled: bool
    receiver_config: dict
    retry_policy: dict
    created_at: datetime
    updated_at: datetime
    stats: dict[str, int] = {}


class EndpointCreated(BaseModel):
    endpoint: EndpointOut
    signing_secret: str


class EventIn(BaseModel):
    event_type: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:\-]+$")
    payload: dict[str, Any]
    endpoint_ids: list[uuid.UUID] = Field(min_length=1, max_length=5)
    idempotency_key: str | None = Field(None, min_length=1, max_length=200)
    metadata: dict[str, str] = Field(default_factory=dict, max_length=20)


class DeliverySummary(ORM):
    id: uuid.UUID
    endpoint_id: uuid.UUID
    status: str
    attempt_count: int
    next_attempt_at: datetime | None
    completed_at: datetime | None
    last_error_category: str | None


class EventListItem(BaseModel):
    id: uuid.UUID
    event_type: str
    created_at: datetime
    simulation_run_id: uuid.UUID | None = None
    idempotency_key: str | None
    correlation_id: str
    source: str
    status: str
    deliveries: list[DeliverySummary]


class EventList(BaseModel):
    items: list[EventListItem]
    next_cursor: str | None


class AttemptOut(ORM):
    id: uuid.UUID
    delivery_id: uuid.UUID
    attempt_number: int
    request_started_at: datetime
    request_completed_at: datetime
    duration_ms: int
    request_headers: dict
    response_status: int | None
    response_headers: dict
    response_body_excerpt: str | None
    error_category: str | None
    error_message: str | None
    outcome: str
    worker_id: str


class TransitionOut(ORM):
    delivery_id: uuid.UUID
    from_status: str | None
    to_status: str
    reason: str
    created_at: datetime


class DeliveryOut(DeliverySummary):
    endpoint_name: str
    retry_policy: dict
    replay_of_delivery_id: uuid.UUID | None
    created_at: datetime
    dead_letter_reason: str | None = None


class EventDetail(BaseModel):
    id: uuid.UUID
    simulation_run_id: uuid.UUID | None = None
    event_type: str
    payload: dict
    metadata: dict
    idempotency_key: str | None
    correlation_id: str
    source: str
    created_at: datetime
    status: str
    deliveries: list[DeliveryOut]
    attempts: list[AttemptOut]
    transitions: list[TransitionOut]


ReplayPolicyChoice = Literal["original", "endpoint", "fast", "balanced", "conservative", "custom"]


class ReplayIn(BaseModel):
    policy: ReplayPolicyChoice = "original"
    retry_policy: RetryPolicySpec | None = None


class BulkReplayIn(ReplayIn):
    delivery_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)
    # Must equal the number of selected deliveries: a deliberate confirmation that prevents replay storms.
    confirm_count: int


class ReplayOut(BaseModel):
    replays: list[DeliverySummary]


class ReplayRef(ORM):
    id: uuid.UUID
    status: str
    attempt_count: int
    created_at: datetime


class DeadLetterItem(BaseModel):
    id: uuid.UUID
    delivery_id: uuid.UUID
    event_id: uuid.UUID
    event_type: str
    endpoint_id: uuid.UUID
    endpoint_name: str
    reason: str
    attempt_count: int
    last_error_category: str | None
    last_status: int | None
    created_at: datetime
    resolved_at: datetime | None
    resolution_note: str | None
    replays: list[ReplayRef]


class DeadLetterList(BaseModel):
    items: list[DeadLetterItem]
    counts: dict[str, int]
    next_before: datetime | None


class DeadLetterDetail(DeadLetterItem):
    payload: dict
    retry_policy: dict
    attempts: list[AttemptOut]


class ResolveIn(BaseModel):
    note: str = Field("", max_length=500)


class IdempotencyRecordOut(ORM):
    idempotency_key: str
    first_delivery_id: uuid.UUID | None
    first_seen_at: datetime
    last_seen_at: datetime
    seen_count: int
    processing_result: str
    expires_at: datetime


class AuditOut(ORM):
    id: int
    actor: str
    action: str
    resource_type: str
    resource_id: str | None
    metadata: dict = Field(validation_alias="metadata_")
    created_at: datetime


class SimulationIn(BaseModel):
    name: str | None = Field(None, max_length=120)
    preset: str | None = Field(None, max_length=40)
    config: SimulationConfig


class SimulationOut(ORM):
    id: uuid.UUID
    name: str
    preset: str | None
    status: str
    configuration: dict
    random_seed: int
    event_count: int
    delivery_count: int
    started_at: datetime
    deadline_at: datetime
    completed_at: datetime | None
    end_reason: str | None
    created_at: datetime
    summary: dict | None = None


class PresetOut(BaseModel):
    key: str
    title: str
    description: str
    config: dict


class CompareIn(BaseModel):
    run_ids: list[uuid.UUID] = Field(min_length=2, max_length=4)


class ConfigDifference(BaseModel):
    key: str
    values: list[Any]


class CompareOut(BaseModel):
    runs: list[SimulationOut]
    config_differences: list[ConfigDifference]
