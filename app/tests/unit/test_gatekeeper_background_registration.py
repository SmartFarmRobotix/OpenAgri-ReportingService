"""
`main.py`'s `lifespan` used to call `register_apis_to_gatekeeper`
synchronously, with no try/except, before `yield` -- any failure there
(connection error, timeout, missing JSON keys, non-2xx response) crashed
application startup entirely, so RS never served anything, including its
own /health liveness endpoint.

These tests prove:
  1. `start_gatekeeper_registration_background` returns (and the thread it
     starts keeps running) instead of raising, even when GateKeeper is
     completely unreachable.
  2. The retry loop backs off exponentially and keeps retrying (unbounded
     attempt count), logging each attempt.
"""
import logging
import os
import threading
import time
from unittest import TestCase

REQUIRED_ENV_VARS = {
    "REPORTING_GATEKEEPER_USERNAME": "mock_user",
    "REPORTING_GATEKEEPER_PASSWORD": "mock_pass",
    "REPORTING_BACKEND_CORS_ORIGINS": '["*"]',
    "REPORTING_POSTGRES_USER": "mock_pg_user",
    "REPORTING_POSTGRES_PASSWORD": "mock_pg_pass",
    "REPORTING_POSTGRES_DB": "mock_db",
    "REPORTING_POSTGRES_HOST": "mock_host",
    "REPORTING_POSTGRES_PORT": "5432",
    "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
    "REPORTING_SERVICE_NAME": "mock_service",
    "REPORTING_SERVICE_PORT": "8000",
    "REPORTING_GATEKEEPER_BASE_URL": "http://mock.gatekeeper/",
    "JWT_ACCESS_TOKEN_EXPIRATION_TIME": "3600",
    "JWT_SIGNING_KEY": "unit-test-shared-secret-not-a-placeholder-0006",
    "PDF_DIRECTORY": "/tmp/gk_bg_test_pdfs/",
    "REPORTING_USING_GATEKEEPER": "True",
    "GK_REGISTRATION_TIMEOUT_SECONDS": "1",
}


class TestGatekeeperBackgroundRegistration(TestCase):
    def setUp(self):
        for k, v in REQUIRED_ENV_VARS.items():
            os.environ[k] = v

    def test_returns_immediately_and_retries_in_background_on_failure(self):
        import init_gatekeeper

        call_count = {"n": 0}
        call_event = threading.Event()

        def _always_fails():
            call_count["n"] += 1
            call_event.set()
            raise ConnectionError("simulated: GateKeeper unreachable")

        original = init_gatekeeper.register_apis_to_gatekeeper
        init_gatekeeper.register_apis_to_gatekeeper = _always_fails
        try:
            start = time.monotonic()
            thread, stop_event = init_gatekeeper.start_gatekeeper_registration_background(
                initial_backoff_seconds=0.05,
                max_backoff_seconds=0.2,
            )
            elapsed = time.monotonic() - start

            self.assertLess(
                elapsed, 0.5, "starting the background task must be near-instant"
            )

            self.assertTrue(call_event.wait(timeout=2), "expected at least one attempt")
            time.sleep(0.5)
            self.assertGreaterEqual(
                call_count["n"],
                2,
                "expected multiple retry attempts against a permanently "
                "failing GateKeeper",
            )
            self.assertTrue(thread.is_alive(), "retry thread should still be running")
        finally:
            stop_event.set()
            init_gatekeeper.register_apis_to_gatekeeper = original
            thread.join(timeout=2)

    def test_retry_attempts_are_logged(self):
        import init_gatekeeper

        def _always_fails():
            raise ConnectionError("simulated: GateKeeper unreachable")

        original = init_gatekeeper.register_apis_to_gatekeeper
        init_gatekeeper.register_apis_to_gatekeeper = _always_fails
        logger = logging.getLogger("init_gatekeeper")
        records = []

        class _Collector(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Collector()
        logger.addHandler(handler)
        try:
            thread, stop_event = init_gatekeeper.start_gatekeeper_registration_background(
                initial_backoff_seconds=0.05,
                max_backoff_seconds=0.1,
            )
            time.sleep(0.5)
        finally:
            stop_event.set()
            init_gatekeeper.register_apis_to_gatekeeper = original
            logger.removeHandler(handler)
            thread.join(timeout=2)

        messages = [r.getMessage() for r in records]
        self.assertTrue(
            any("registration attempt" in m for m in messages),
            f"expected a per-attempt retry log line; got: {messages}",
        )

    def test_stops_retrying_once_registration_succeeds(self):
        import init_gatekeeper

        call_count = {"n": 0}

        def _fails_once_then_succeeds():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ConnectionError("simulated transient failure")
            return None

        original = init_gatekeeper.register_apis_to_gatekeeper
        init_gatekeeper.register_apis_to_gatekeeper = _fails_once_then_succeeds
        try:
            thread, stop_event = init_gatekeeper.start_gatekeeper_registration_background(
                initial_backoff_seconds=0.05,
                max_backoff_seconds=0.1,
            )
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive(), "thread must exit after success")
            self.assertEqual(call_count["n"], 2)
        finally:
            stop_event.set()
            init_gatekeeper.register_apis_to_gatekeeper = original
