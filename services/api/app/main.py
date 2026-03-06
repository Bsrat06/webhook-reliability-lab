import logging
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import deliveries, endpoints, events, receiver, session
from app.core.config import get_settings
from app.core.errors import ServiceError
from app.db.session import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
settings = get_settings()
app = FastAPI(
    title="Webhook Reliability Lab API",
    version="0.1.0",
    docs_url=None if settings.is_production else "/docs",
)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Cookie-authenticated mutations require a custom header (forces CORS preflight) and a trusted Origin."""
    if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
        if request.headers.get("x-requested-with") != "fetch":
            return _error(403, "csrf", "Missing X-Requested-With header.")
        origin = request.headers.get("origin")
        if origin:
            o = urlparse(origin)
            if f"{o.scheme}://{o.netloc}" not in settings.origins:
                return _error(403, "csrf", "Untrusted request origin.")
    return await call_next(request)


def _error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message, **extra}}, status_code=status)


@app.exception_handler(ServiceError)
async def service_error(_: Request, exc: ServiceError):
    return _error(exc.status, exc.code, exc.message, **exc.extra)


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    return _error(exc.status_code, "http_error", str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    fields = [{"loc": [str(p) for p in e["loc"]], "message": e["msg"]} for e in exc.errors()]
    return _error(422, "validation_error", "Request validation failed.", fields=fields)


@app.get("/health/live", include_in_schema=False)
def live():
    return {"status": "ok"}


@app.get("/health/ready", include_in_schema=False)
def ready():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    return {"status": "ok", "database": "up"}


for r in (session.router, endpoints.router, events.router, deliveries.router, receiver.router):
    app.include_router(r)
