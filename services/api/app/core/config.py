from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str = "postgresql+psycopg://app:app@localhost:5432/app"
    # Used to derive the key that encrypts endpoint signing secrets at rest.
    secret_key: str = "dev-only-change-me"

    # Base URL the worker uses to reach the built-in receiver over real HTTP.
    receiver_base_url: str = "http://localhost:8000"
    # Allowed browser origins for state-changing requests (CSRF Origin check).
    allowed_origins: str = "http://localhost:3000"

    session_cookie_name: str = "wh_session"
    guest_retention_hours: int = 24
    # New guest sessions allowed per client IP per 10 minutes.
    guest_sessions_per_ip: int = 10

    # Guest quotas (spec §2.4); configurable per environment.
    quota_endpoints: int = 5
    quota_payload_bytes: int = 256 * 1024
    quota_events_per_hour: int = 2000
    quota_replays_per_hour: int = 500
    quota_bulk_replay: int = 50
    quota_workspace_concurrency: int = 4
    quota_concurrent_simulations: int = 1
    quota_simulation_events: int = 1000
    quota_simulation_events_per_hour: int = 10_000
    quota_simulation_runtime_seconds: int = 300
    global_concurrent_simulations: int = 20
    quota_export_rows: int = 10_000
    idempotency_retention_hours: int = 24
    cleanup_interval_seconds: int = 60

    # Delivery engine bounds.
    delivery_timeout_seconds: float = 10.0
    response_excerpt_bytes: int = 2048
    worker_lease_seconds: int = 30
    worker_batch_size: int = 10
    worker_poll_interval_seconds: float = 0.5

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def origins(self) -> set[str]:
        return {o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
