export type DeliveryStatus = "pending" | "processing" | "retry_scheduled" | "succeeded" | "dead_lettered" | "cancelled";
export type EventStatus = "in_progress" | "delivered" | "partially_failed" | "failed" | "no_deliveries";

export interface Session {
  workspace: { id: string; name: string; workspace_type: string; created_at: string; expires_at: string | null };
  retention_hours: number;
  limits: Record<string, number>;
  usage: Record<string, number>;
  worker: { online: boolean; last_heartbeat_at: string | null; due_jobs: number; oldest_due_seconds: number };
}

export type FailureKind = "server_error" | "rate_limited" | "timeout" | "connection_drop";

export interface FailureMix {
  server_error: number;
  rate_limited: number;
  timeout: number;
  connection_drop: number;
}

export interface ReceiverConfig {
  mode: "succeed" | "fail" | "fail_first_n" | "random";
  failure_kind: FailureKind;
  failure_status: number;
  fail_first_n: number;
  failure_rate: number;
  failure_mix: FailureMix;
  retry_after_s: number;
  ack_loss_rate: number;
  idempotency: boolean;
  latency_ms_min: number;
  latency_ms_max: number;
  seed: number;
}

export interface RetryPolicy {
  max_attempts: number;
  initial_delay_ms: number;
  multiplier: number;
  max_delay_ms: number;
  jitter: "none" | "full" | "equal";
  retry_on_status: number[];
  retry_on_network_errors: boolean;
  timeout_ms: number;
  respect_retry_after: boolean;
}

export interface Endpoint {
  id: string;
  name: string;
  description: string;
  endpoint_type: "builtin" | "external";
  destination_url: string | null;
  enabled: boolean;
  receiver_config: ReceiverConfig;
  retry_policy: RetryPolicy;
  created_at: string;
  updated_at: string;
  stats: Partial<Record<DeliveryStatus, number>>;
}

export interface DeliverySummary {
  id: string;
  endpoint_id: string;
  status: DeliveryStatus;
  attempt_count: number;
  next_attempt_at: string | null;
  completed_at: string | null;
  last_error_category: string | null;
}

export interface EventListItem {
  id: string;
  event_type: string;
  created_at: string;
  idempotency_key: string | null;
  correlation_id: string;
  source: string;
  simulation_run_id: string | null;
  status: EventStatus;
  deliveries: DeliverySummary[];
}

export interface Attempt {
  id: string;
  delivery_id: string;
  attempt_number: number;
  request_started_at: string;
  request_completed_at: string;
  duration_ms: number;
  request_headers: Record<string, string>;
  response_status: number | null;
  response_headers: Record<string, string>;
  response_body_excerpt: string | null;
  error_category: string | null;
  error_message: string | null;
  outcome: "success" | "retryable_failure" | "permanent_failure";
  worker_id: string;
}

export interface Transition {
  delivery_id: string;
  from_status: string | null;
  to_status: DeliveryStatus;
  reason: string;
  created_at: string;
}

export interface Delivery extends DeliverySummary {
  endpoint_name: string;
  retry_policy: RetryPolicy;
  replay_of_delivery_id: string | null;
  created_at: string;
  dead_letter_reason: string | null;
}

export interface EventDetail {
  id: string;
  simulation_run_id: string | null;
  event_type: string;
  payload: unknown;
  metadata: Record<string, string>;
  idempotency_key: string | null;
  correlation_id: string;
  source: string;
  created_at: string;
  status: EventStatus;
  deliveries: Delivery[];
  attempts: Attempt[];
  transitions: Transition[];
}

export interface EventList {
  items: EventListItem[];
  next_cursor: string | null;
}

export type ReplayPolicy = "original" | "endpoint" | "fast" | "balanced" | "conservative";

export interface ReplayRef {
  id: string;
  status: DeliveryStatus;
  attempt_count: number;
  created_at: string;
}

export interface DeadLetter {
  id: string;
  delivery_id: string;
  event_id: string;
  event_type: string;
  endpoint_id: string;
  endpoint_name: string;
  reason: string;
  attempt_count: number;
  last_error_category: string | null;
  last_status: number | null;
  created_at: string;
  resolved_at: string | null;
  resolution_note: string | null;
  replays: ReplayRef[];
}

export interface DeadLetterList {
  items: DeadLetter[];
  counts: { open: number; resolved: number };
  next_before: string | null;
}

export interface IdempotencyRecord {
  idempotency_key: string;
  first_delivery_id: string | null;
  first_seen_at: string;
  last_seen_at: string;
  seen_count: number;
  processing_result: string;
  expires_at: string;
}

export interface AuditEntry {
  id: number;
  actor: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface SimulationConfig {
  event_count: number;
  endpoint_ids: string[];
  event_type: string;
  emit_over_ms: number;
  concurrency: number;
  receiver: ReceiverConfig;
  retry_policy: RetryPolicy;
  use_idempotency_keys: boolean;
  duplicate_send_rate: number;
  max_runtime_s: number;
  seed: number;
}

export interface Percentiles {
  n: number;
  p50: number | null;
  p95: number | null;
  p99: number | null;
  insufficient: boolean;
  mean?: number | null;
}

export interface SimulationSummary {
  deliveries: number;
  events: number;
  by_status: Record<DeliveryStatus, number>;
  finished: number;
  progress: number;
  success_rate: number | null;
  dead_letter_rate: number | null;
  attempts: number;
  retries: number;
  attempts_per_delivery: number | null;
  duplicates_suppressed: number;
  failure_categories: Record<string, number>;
  attempt_distribution: Record<string, number>;
  latency_ms: Percentiles;
  delivery_time_ms: Percentiles;
  elapsed_ms: number;
}

export type SimulationStatus = "running" | "completed" | "cancelled" | "timed_out";

export interface SimulationRun {
  id: string;
  name: string;
  preset: string | null;
  status: SimulationStatus;
  configuration: SimulationConfig;
  random_seed: number;
  event_count: number;
  delivery_count: number;
  started_at: string;
  deadline_at: string;
  completed_at: string | null;
  end_reason: string | null;
  created_at: string;
  summary: SimulationSummary | null;
}

export interface Preset {
  key: string;
  title: string;
  description: string;
  config: Omit<SimulationConfig, "endpoint_ids">;
}

export interface Comparison {
  runs: SimulationRun[];
  config_differences: { key: string; values: unknown[] }[];
}
