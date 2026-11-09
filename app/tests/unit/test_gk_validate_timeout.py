"""
`api.deps.get_current_user`'s call to GateKeeper's `validate_token` used to
have no timeout and no defined failure behavior -- a hung GateKeeper could
hang every authenticated RS request indefinitely. It now passes
`timeout=settings.REPORTING_GATEKEEPER_TIMEOUT_SECONDS` and maps the two
distinct `requests` failure modes to distinct, defined HTTP responses:

  `requests.exceptions.Timeout` (GK simulated as hung) -> 504
  any other `requests.exceptions.RequestException`
    (GK unreachable/connection refused) -> 503

Both are exercised here without any real network call or live GateKeeper.
"""
import os
from unittest import TestCase
from unittest.mock import patch

import requests
from fastapi import HTTPException

REQUIRED_ENV_VARS = {
    "REPORTING_GATEKEEPER_USERNAME": "mock_user",
    "REPORTING_GATEKEEPER_PASSWORD": "mock_pass",
    "REPORTING_BACKEND_CORS_ORIGINS": '["*"]',
    "REPORTING_POSTGRES_USER": "mock_pg_user",
    "REPORTING_POSTGRES_PASSWORD": "mock_pg_pass",
    "REPORTING_POSTGRES_DB": "mock_db",
    "REPORTING_POSTGRES_HOST": "mock_host",
    "REPORTING_POSTGRES_PORT": "5432",
    "SQLALCHEMY_DATABASE_URI": "sqlite:///./test.db",
    "REPORTING_SERVICE_NAME": "mock_service",
    "REPORTING_SERVICE_PORT": "8000",
    "REPORTING_GATEKEEPER_BASE_URL": "http://mock.gatekeeper/",
    "JWT_ACCESS_TOKEN_EXPIRATION_TIME": "3600",
    "JWT_SIGNING_KEY": "unit-test-shared-secret-not-a-placeholder-0007",
    "PDF_DIRECTORY": "/tmp/gk_timeout_test_pdfs/",
    "REPORTING_USING_GATEKEEPER": "True",
    "REPORTING_GATEKEEPER_TIMEOUT_SECONDS": "5",
}


class TestGatekeeperValidateTokenTimeout(TestCase):
    @classmethod
    def setUpClass(cls):
        for k, v in REQUIRED_ENV_VARS.items():
            os.environ[k] = v
        super().setUpClass()

        from core.config import settings
        from api import deps

        cls.settings = settings
        cls.deps = deps

    def _call_with_post_side_effect(self, side_effect):
        with patch.object(
            self.settings, "REPORTING_USING_GATEKEEPER", True
        ), patch.object(self.deps, "requests") as mock_requests:
            mock_requests.exceptions = requests.exceptions
            mock_requests.post.side_effect = side_effect
            return self.deps.get_current_user(db=None, token="irrelevant-token")

    def test_gk_call_passes_configured_timeout(self):
        with patch.object(
            self.settings, "REPORTING_USING_GATEKEEPER", True
        ), patch.object(self.deps, "requests") as mock_requests:
            mock_requests.exceptions = requests.exceptions
            mock_requests.post.side_effect = requests.exceptions.Timeout()
            try:
                self.deps.get_current_user(db=None, token="irrelevant-token")
            except HTTPException:
                pass
            _, kwargs = mock_requests.post.call_args
            self.assertEqual(
                kwargs.get("timeout"),
                self.settings.REPORTING_GATEKEEPER_TIMEOUT_SECONDS,
                "GK validate_token call must be made with the configured timeout",
            )

    def test_hung_gatekeeper_fails_with_504_within_configured_timeout(self):
        with self.assertRaises(HTTPException) as ctx:
            self._call_with_post_side_effect(requests.exceptions.Timeout())
        self.assertEqual(ctx.exception.status_code, 504)

    def test_unreachable_gatekeeper_fails_with_503(self):
        with self.assertRaises(HTTPException) as ctx:
            self._call_with_post_side_effect(
                requests.exceptions.ConnectionError("simulated: connection refused")
            )
        self.assertEqual(ctx.exception.status_code, 503)
