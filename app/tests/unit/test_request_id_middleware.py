import logging
import os

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
    "REPORTING_GATEKEEPER_BASE_URL": "http://mock.gatekeeper",
    "JWT_ACCESS_TOKEN_EXPIRATION_TIME": "3600",
    "JWT_SIGNING_KEY": "unit-test-shared-secret-not-a-placeholder-0003",
    "PDF_DIRECTORY": "/tmp/request_id_test_pdfs/",
    "REPORTING_USING_GATEKEEPER": "False",
}


def _client():
    for k, v in REQUIRED_ENV_VARS.items():
        os.environ[k] = v

    from main import app
    from fastapi.testclient import TestClient

    return TestClient(app)


def test_known_request_id_is_echoed_and_logged(caplog):
    client = _client()
    known_id = "test-request-id-abc123"

    with caplog.at_level(logging.INFO):
        response = client.get("/health", headers={"X-Request-ID": known_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == known_id

    tagged = [
        r for r in caplog.records if getattr(r, "request_id", None) == known_id
    ]
    assert tagged, (
        "expected at least one log record carrying request_id="
        f"{known_id!r}; saw request_ids="
        f"{[getattr(r, 'request_id', None) for r in caplog.records]}"
    )
    assert any("request started" in r.getMessage() for r in tagged)
    assert any("request finished" in r.getMessage() for r in tagged)


def test_missing_request_id_gets_a_generated_one(caplog):
    client = _client()

    with caplog.at_level(logging.INFO):
        response = client.get("/health")

    assert response.status_code == 200
    generated_id = response.headers["X-Request-ID"]
    assert generated_id and generated_id != "-"

    tagged = [
        r for r in caplog.records if getattr(r, "request_id", None) == generated_id
    ]
    assert tagged, "expected log records tagged with the generated request id"


def test_records_outside_a_request_default_to_dash():
    logger = logging.getLogger("app.request.default_test")
    record = logger.makeRecord(
        logger.name, logging.INFO, __file__, 0, "no request in flight", (), None
    )
    assert record.request_id == "-"
