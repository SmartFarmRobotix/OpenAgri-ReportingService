"""A log file is written for every run, and it records socket and resolver attempts."""
from __future__ import annotations

import os
import socket
from unittest import TestCase

_RS_ENV = {
    "REPORTING_GATEKEEPER_USERNAME": "admin",
    "REPORTING_GATEKEEPER_PASSWORD": "admin",
    "REPORTING_BACKEND_CORS_ORIGINS": '["http://localhost:3000"]',
    "REPORTING_POSTGRES_USER": "test",
    "REPORTING_POSTGRES_PASSWORD": "test",
    "REPORTING_POSTGRES_DB": "test",
    "REPORTING_POSTGRES_HOST": "localhost",
    "REPORTING_POSTGRES_PORT": "5432",
    "SQLALCHEMY_DATABASE_URI": "sqlite:///./test.db",
    "REPORTING_SERVICE_NAME": "reporting",
    "REPORTING_SERVICE_PORT": "8002",
    "REPORTING_GATEKEEPER_BASE_URL": "http://gatekeeper:8000",
    "JWT_ACCESS_TOKEN_EXPIRATION_TIME": "3600",
    "JWT_SIGNING_KEY": "test-signing-key-not-a-real-secret",
    "PDF_DIRECTORY": "/tmp/",
    "REPORTING_USING_GATEKEEPER": "False",
}
for _k, _v in _RS_ENV.items():
    os.environ.setdefault(_k, _v)


class TestConnectionAudit(TestCase):
    def setUp(self):
        from core import connection_audit

        self.connection_audit = connection_audit
        connection_audit._reset_for_tests()

    def tearDown(self):
        self.connection_audit._reset_for_tests()

    def test_install_is_noop_without_env_var(self):
        os.environ.pop("CONN_AUDIT_LOG_DIR", None)
        self.assertIsNone(self.connection_audit.install("reporting"))
        self.assertIsNone(self.connection_audit.current_log_path())

    def test_install_produces_an_empty_artifact_even_with_zero_attempts(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CONN_AUDIT_LOG_DIR"] = tmp
            try:
                path = self.connection_audit.install("reporting")
                self.assertIsNotNone(path)
                self.assertTrue(path.exists())
                self.assertEqual(self.connection_audit.attempt_count(), 0)
            finally:
                os.environ.pop("CONN_AUDIT_LOG_DIR", None)

    def test_connect_attempt_is_recorded(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CONN_AUDIT_LOG_DIR"] = tmp
            try:
                self.connection_audit.install("reporting")
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                try:
                    try:
                        sock.connect(("127.0.0.1", 1))
                    except OSError:
                        pass
                finally:
                    sock.close()
                self.assertEqual(self.connection_audit.attempt_count(), 1)
            finally:
                os.environ.pop("CONN_AUDIT_LOG_DIR", None)
