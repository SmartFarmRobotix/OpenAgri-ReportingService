import json
import threading
import time

import requests
import logging

from fastapi import APIRouter
from core.config import settings
from api.api_v1.endpoints import report


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def register_apis_to_gatekeeper():
    at = requests.post(
        url=settings.REPORTING_GATEKEEPER_BASE_URL + "api/login/",
        json={
            "username": "{}".format(settings.REPORTING_GATEKEEPER_USERNAME),
            "password": "{}".format(settings.REPORTING_GATEKEEPER_PASSWORD),
        },
        timeout=settings.GK_REGISTRATION_TIMEOUT_SECONDS,
    )
    temp = at.json()
    access = temp["access"]
    refresh = temp["refresh"]

    # Registration  of APIs
    apis_to_register = APIRouter()
    apis_to_register.include_router(report.router, prefix="/openagri-report")
    for api in apis_to_register.routes:
        api_response = None
        try:
            json_data = {
                "base_url": "http://{}:{}/".format(
                    settings.REPORTING_SERVICE_NAME, settings.REPORTING_SERVICE_PORT
                ),
                "service_name": settings.REPORTING_SERVICE_NAME,
                "endpoint": f"api/v1/{api.path.strip('/')}",
                "methods": list(api.methods),
            }
            logger.info("Port registered: %s", settings.REPORTING_SERVICE_PORT)
            api_response = requests.post(
                url=settings.REPORTING_GATEKEEPER_BASE_URL + "api/register_service/",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer {}".format(access),
                },
                json=json_data,
                timeout=settings.GK_REGISTRATION_TIMEOUT_SECONDS,
            )

        except Exception as e:
            logger.info(f"Failed to register API to gatekeeper. {e}")
            return

        if str(api_response.status_code)[0] != "2":
            logger.info(
                f"API api/v1/{api.path.strip('/')} failed with registration to gatekeeper"
            )

    requests.post(
        url=settings.REPORTING_GATEKEEPER_BASE_URL + "api/logout/",
        json={"refresh": refresh},
        timeout=settings.GK_REGISTRATION_TIMEOUT_SECONDS,
    )
    return


def _register_apis_to_gatekeeper_with_retry(
    stop_event: threading.Event,
    initial_backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 60.0,
) -> None:
    """
    Retry register_apis_to_gatekeeper with capped exponential backoff and
    an unbounded attempt count, until it succeeds (or the caller asks us
    to stop). Registration isn't time-critical -- GateKeeper being slow,
    unreachable, or briefly returning malformed responses at startup must
    never prevent RS from starting up and serving requests (including its
    own /health liveness endpoint).
    """
    backoff = initial_backoff_seconds
    attempt = 0
    while not stop_event.is_set():
        attempt += 1
        try:
            register_apis_to_gatekeeper()
        except Exception as e:
            logger.warning(
                "GateKeeper registration attempt %d failed (%s); retrying in %.1fs.",
                attempt,
                e,
                backoff,
            )
            stop_event.wait(backoff)
            backoff = min(backoff * 2, max_backoff_seconds)
            continue

        logger.info(
            "Successfully registered APIs with GateKeeper after %d attempt(s).",
            attempt,
        )
        return


def start_gatekeeper_registration_background(
    initial_backoff_seconds: float = 1.0,
    max_backoff_seconds: float = 60.0,
) -> tuple[threading.Thread, threading.Event]:
    """
    Kick off GateKeeper registration on a background daemon thread so
    application startup (FastAPI's `lifespan`) is never blocked by, or
    fails because of, a slow/unreachable/misbehaving GateKeeper.

    RS uses the synchronous `requests` library throughout; rather than
    running blocking HTTP calls inside an asyncio task (which would stall
    the event loop for the duration of each call), registration runs on a
    plain background thread with its own retry+backoff loop. The event
    loop is never blocked by it.

    Returns the (daemon) thread and a stop Event a caller can set to ask
    the retry loop to exit early (e.g. during tests or a graceful
    shutdown); the thread is a daemon so it never blocks process exit on
    its own.
    """
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_register_apis_to_gatekeeper_with_retry,
        args=(stop_event,),
        kwargs={
            "initial_backoff_seconds": initial_backoff_seconds,
            "max_backoff_seconds": max_backoff_seconds,
        },
        name="gatekeeper-registration",
        daemon=True,
    )
    thread.start()
    return thread, stop_event
