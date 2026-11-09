import os
import tempfile
from unittest import TestCase
from unittest.mock import MagicMock, patch

_TMP_DB_PATH = os.path.join(tempfile.gettempdir(), "rs_test_health.db")

REQUIRED_ENV_VARS = {
    "REPORTING_GATEKEEPER_USERNAME": "mock_user",
    "REPORTING_GATEKEEPER_PASSWORD": "mock_pass",
    "REPORTING_BACKEND_CORS_ORIGINS": '["*"]',
    "REPORTING_POSTGRES_USER": "mock_pg_user",
    "REPORTING_POSTGRES_PASSWORD": "mock_pg_pass",
    "REPORTING_POSTGRES_DB": "mock_db",
    "REPORTING_POSTGRES_HOST": "mock_host",
    "REPORTING_POSTGRES_PORT": "5432",
    "SQLALCHEMY_DATABASE_URI": f"sqlite:///{_TMP_DB_PATH}",
    "REPORTING_SERVICE_NAME": "mock_service",
    "REPORTING_SERVICE_PORT": "8000",
    "REPORTING_GATEKEEPER_BASE_URL": "http://mock.gatekeeper",
    "JWT_ACCESS_TOKEN_EXPIRATION_TIME": "3600",
    "JWT_SIGNING_KEY": "unit-test-shared-secret-not-a-placeholder-0002",
    "PDF_DIRECTORY": "/tmp/health_test_pdfs/",
    "REPORTING_USING_GATEKEEPER": "False",
}


class TestHealthEndpoints(TestCase):
    def setUp(self):
        for k, v in REQUIRED_ENV_VARS.items():
            os.environ[k] = v

        from main import app
        from fastapi.testclient import TestClient

        self.app = app
        self.client = TestClient(app)

    def test_health_is_2xx_with_db_up(self):
        response = self.client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_ready_is_2xx_with_db_up(self):
        response = self.client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_ready_is_degraded_when_db_unreachable(self):
        from api import health

        with patch.object(health, "SessionLocal") as mock_session_local:
            mock_session_local.side_effect = Exception("connection refused")
            response = self.client.get("/ready")

        assert response.status_code == 503
        assert response.json()["detail"]["status"] == "degraded"

    def test_health_is_still_2xx_when_db_unreachable(self):
        """Health must not depend on the DB at all."""
        from api import health

        with patch.object(health, "SessionLocal") as mock_session_local:
            mock_session_local.side_effect = Exception("connection refused")
            response = self.client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
