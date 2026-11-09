"""
`core.config.Settings` must fail construction -- not silently start -- when
`JWT_SIGNING_KEY` is a known placeholder value. Exercises the pydantic
`field_validator` directly (`Settings(...)`), which is exactly what runs
the moment the process-wide `settings = Settings` singleton is
constructed at import time; a placeholder value here means RS never binds
its port.
"""
import os
from unittest import TestCase

from pydantic import ValidationError

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
    "JWT_SIGNING_KEY": "unit-test-shared-secret-not-a-placeholder-0008",
    "PDF_DIRECTORY": "/tmp/placeholder_secret_test_pdfs/",
    "REPORTING_USING_GATEKEEPER": "True",
}


class TestPlaceholderJWTSigningKeyRejection(TestCase):
    @classmethod
    def setUpClass(cls):
        for k, v in REQUIRED_ENV_VARS.items():
            os.environ[k] = v
        super().setUpClass()

        from core.config import Settings, PLACEHOLDER_JWT_SIGNING_KEYS

        cls.Settings = Settings
        cls.placeholders = PLACEHOLDER_JWT_SIGNING_KEYS

    def test_each_known_placeholder_value_is_rejected(self):
        for placeholder in self.placeholders:
            with self.subTest(placeholder=placeholder):
                with self.assertRaises(ValidationError):
                    self.Settings(JWT_SIGNING_KEY=placeholder)

    def test_real_shaped_secret_is_accepted(self):
        settings = self.Settings(
            JWT_SIGNING_KEY="a-real-unique-signing-secret-not-on-the-blocklist"
        )
        self.assertEqual(
            settings.JWT_SIGNING_KEY,
            "a-real-unique-signing-secret-not-on-the-blocklist",
        )
