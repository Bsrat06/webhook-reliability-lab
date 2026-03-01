class ServiceError(Exception):
    """Domain error rendered as a structured JSON response: {"error": {"code", "message", ...}}."""

    def __init__(self, status: int, code: str, message: str, **extra: object) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


def not_found(resource: str) -> ServiceError:
    return ServiceError(404, "not_found", f"{resource} not found.")


def quota_exceeded(resource: str, limit: int, hint: str) -> ServiceError:
    return ServiceError(429, "quota_exceeded", f"{resource} limit reached ({limit}). {hint}", limit=limit)
