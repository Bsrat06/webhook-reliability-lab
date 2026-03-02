"""Integration fixtures: a real PostgreSQL test database and a live API server for the built-in receiver."""

import os
import socket
import threading
import time

import psycopg
import pytest

ADMIN_URL = os.environ.get("TEST_ADMIN_DSN", "postgresql://app:app@localhost:5432/app")
TEST_DB = "app_test"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PORT = _free_port()
os.environ["DATABASE_URL"] = f"postgresql+psycopg://app:app@localhost:5432/{TEST_DB}"
os.environ["RECEIVER_BASE_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["ALLOWED_ORIGINS"] = "http://testserver"
os.environ["SECRET_KEY"] = "test-secret"


def _recreate_db() -> None:
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")


@pytest.fixture(scope="session", autouse=True)
def database():
    _recreate_db()
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(scope="session")
def live_server(database):
    import uvicorn

    from app.main import app

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{PORT}"
    server.should_exit = True


@pytest.fixture(autouse=True)
def clean_tables(database):
    from app.api.ratelimit import guest_session_limiter

    guest_session_limiter._hits.clear()
    yield
    from sqlalchemy import text

    from app.db.session import engine

    with engine.begin() as conn:
        conn.execute(text("TRUNCATE workspaces, jobs, worker_heartbeats CASCADE"))


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, headers={"x-requested-with": "fetch"}) as c:
        yield c


@pytest.fixture
def guest(client):
    r = client.post("/api/v1/guest/session")
    assert r.status_code == 201, r.text
    return client


def run_worker_until(predicate, timeout: float = 15.0) -> None:
    """Run a real worker loop in a thread until predicate() is true."""
    from app.workers.run import run

    stop = threading.Event()
    t = threading.Thread(target=run, kwargs={"stop": stop, "concurrency": 4}, daemon=True)
    t.start()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        raise AssertionError("condition not met before timeout")
    finally:
        stop.set()
        t.join(timeout=10)
