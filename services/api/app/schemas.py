import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.receiver import ReceiverBehavior
from app.domain.retry import RetryPolicySpec


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
    idempotency_key: str | None
    correlation_id: str
    source: str
    status: str
    deliveries: list[DeliverySummary]


class EventList(BaseModel):
    items: list[EventListItem]
    next_before: datetime | None


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
