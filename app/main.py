import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from core.config import settings

from core.request_context import request_id_var  # noqa: F401
from core import connection_audit
from core.middleware import RequestIDMiddleware
from api.api_v1.api import api_router
from api.health import router as health_router
from init_gatekeeper import start_gatekeeper_registration_background

_LOG_FORMAT = "%(asctime)s %(levelname)s [request_id=%(request_id)s] %(name)s: %(message)s"
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
if not _root_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    _root_logger.addHandler(_handler)
else:
    for _handler in _root_logger.handlers:
        _handler.setFormatter(logging.Formatter(_LOG_FORMAT))


@asynccontextmanager
async def lifespan(fa: FastAPI):
    connection_audit.install(settings.REPORTING_SERVICE_NAME)
    if settings.REPORTING_USING_GATEKEEPER:
        start_gatekeeper_registration_background()
    yield


app = FastAPI(
    title="Reporting service", openapi_url="/api/v1/openapi.json", lifespan=lifespan
)


# Set all CORS enabled origins
if settings.REPORTING_BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.REPORTING_BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.add_middleware(RequestIDMiddleware)

app.include_router(api_router, prefix="/api/v1")
app.include_router(health_router)
