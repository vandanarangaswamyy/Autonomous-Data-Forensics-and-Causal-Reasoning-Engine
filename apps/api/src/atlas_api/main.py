import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from atlas_core.config import Settings
from atlas_core.errors import AtlasError, ErrorResponse
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.responses import Response

logger = logging.getLogger(__name__)


class ProbeResponse(BaseModel):
    status: str


def error_response(request: Request, code: str, message: str, status: int) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    payload = ErrorResponse(code=code, message=message, request_id=request_id)
    return JSONResponse(
        payload.model_dump(), status_code=status, headers={"X-Request-ID": request_id}
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False

    app = FastAPI(title="ATLAS", version="0.1.0", lifespan=lifespan)
    app.state.settings = config
    app.state.ready = False

    @app.middleware("http")
    async def correlate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(AtlasError)
    async def domain_error(request: Request, exc: AtlasError) -> JSONResponse:
        return error_response(request, exc.code, exc.message, exc.status_code)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(request, "http_error", "Request could not be served", exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(request, "validation_error", "Request validation failed", 422)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled API error request_id=%s", request.state.request_id)
        return error_response(request, "internal_error", "Internal server error", 500)

    @app.get("/health", response_model=ProbeResponse)
    async def health() -> ProbeResponse:
        return ProbeResponse(status="ok")

    @app.get("/ready", response_model=ProbeResponse, responses={503: {"model": ErrorResponse}})
    async def ready(request: Request) -> ProbeResponse:
        if not request.app.state.ready:
            raise AtlasError("not_ready", "Application startup is incomplete", 503)
        return ProbeResponse(status="ready")

    return app
