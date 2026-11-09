"""
A duplicate submission returns the existing report, no new row.
"""

import io
import os
import shutil
import tempfile
from unittest import TestCase
from unittest.mock import MagicMock

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

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
    "REPORTING_GATEKEEPER_BASE_URL": "http://mock.gatekeeper",
    "JWT_ACCESS_TOKEN_EXPIRATION_TIME": "3600",
    "JWT_SIGNING_KEY": "mock_jwt_secret",
    "PDF_DIRECTORY": "/",
    "REPORTING_USING_GATEKEEPER": "True",
}


class TestEdgeReportIngestEndpoint(TestCase):
    BASE_URL = "/api/v1/openagri-report"

    class _FakeAuthenticatedToken:
        def __init__(self, id):
            self.id = id
            self.user_id = id

    @staticmethod
    def _user_login(token):
        if token == "a-valid-token":
            return TestEdgeReportIngestEndpoint._FakeAuthenticatedToken(id="edge-sync-user")
        if token == "owner-token":
            return TestEdgeReportIngestEndpoint._FakeAuthenticatedToken(id="owner-user")
        raise HTTPException(status_code=401, detail="Not Auth!")

    def setUp(self):
        for k, v in REQUIRED_ENV_VARS.items():
            os.environ[k] = v

        from main import app
        from api.deps import get_current_user, get_db
        from db.base_class import Base
        from models import EdgeReportIngest  # noqa: F401 - registers the table on Base
        from fastapi.testclient import TestClient

        self._pdf_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._pdf_dir, True)

        import core

        self._patch_pdf_directory = MagicMock()
        self._original_pdf_directory = core.settings.PDF_DIRECTORY
        core.settings.PDF_DIRECTORY = self._pdf_dir + os.sep
        self.addCleanup(setattr, core.settings, "PDF_DIRECTORY", self._original_pdf_directory)
        self._original_owner = core.settings.REPORTING_EDGE_REPORT_OWNER_USER_ID
        self.addCleanup(setattr, core.settings, "REPORTING_EDGE_REPORT_OWNER_USER_ID", self._original_owner)

        self._db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_file.close()
        self.addCleanup(os.unlink, self._db_file.name)
        engine = create_engine(f"sqlite:///{self._db_file.name}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        self.db = session_factory()
        self.addCleanup(self.db.close)

        app.dependency_overrides[get_current_user] = TestEdgeReportIngestEndpoint._user_login
        app.dependency_overrides[get_db] = lambda: self.db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

    def _post_report(self, session_id="edge-sess-1", content_hash="hash-1", content=b"%PDF-1.4 fake", token="a-valid-token"):
        return self.client.post(
            f"{self.BASE_URL}/pest-session-report/edge-sync/",
            params={"token": token},
            data={"session_id": session_id, "content_hash": content_hash},
            files={"pdf": ("report.pdf", io.BytesIO(content), "application/pdf")},
        )

    def test_first_submission_stores_pdf_and_row(self):
        from models import EdgeReportIngest

        response = self._post_report()
        assert response.status_code == 200
        body = response.json()
        assert body["deduplicated"] is False
        report_uuid = body["uuid"]

        rows = self.db.query(EdgeReportIngest).all()
        assert len(rows) == 1
        assert rows[0].report_uuid == report_uuid
        assert rows[0].session_id == "edge-sess-1"

        pdf_path = f"{self._pdf_dir}{os.sep}edge-sync-user{os.sep}{report_uuid}.pdf"
        assert os.path.isfile(pdf_path)
        with open(pdf_path, "rb") as fh:
            assert fh.read() == b"%PDF-1.4 fake"

    def test_duplicate_submission_returns_existing_report_no_new_row(self):
        from models import EdgeReportIngest

        first = self._post_report()
        first_uuid = first.json()["uuid"]

        second = self._post_report()
        assert second.status_code == 200
        second_body = second.json()
        assert second_body["uuid"] == first_uuid
        assert second_body["deduplicated"] is True

        rows = self.db.query(EdgeReportIngest).filter_by(session_id="edge-sess-1").all()
        assert len(rows) == 1

    def test_different_session_same_hash_creates_separate_row(self):
        from models import EdgeReportIngest

        self._post_report(session_id="edge-sess-1", content_hash="shared-hash")
        self._post_report(session_id="edge-sess-2", content_hash="shared-hash")

        rows = self.db.query(EdgeReportIngest).all()
        assert len(rows) == 2

    def test_invalid_token_rejected(self):
        response = self._post_report(token="not-a-valid-token")
        assert response.status_code == 401

    def _set_owner(self, value):
        import core

        core.settings.REPORTING_EDGE_REPORT_OWNER_USER_ID = value

    def test_configured_owner_is_the_user_the_report_is_filed_under(self):
        from models import EdgeReportIngest

        self._set_owner("owner-user")

        response = self._post_report()
        assert response.status_code == 200
        report_uuid = response.json()["uuid"]

        pdf_path = f"{self._pdf_dir}{os.sep}owner-user{os.sep}{report_uuid}.pdf"
        assert os.path.isfile(pdf_path)
        assert not os.path.exists(f"{self._pdf_dir}{os.sep}edge-sync-user")
        row = self.db.query(EdgeReportIngest).one()
        assert row.user_id == "owner-user"
        assert row.uploaded_by_user_id == "edge-sync-user"
        assert row.pdf_file_name == f"owner-user/{report_uuid}"

        download = self.client.get(
            f"{self.BASE_URL}/{report_uuid}/", params={"token": "owner-token"}
        )
        assert download.status_code == 200
        assert download.content == b"%PDF-1.4 fake"

    def test_without_the_setting_the_uploader_owns_the_report(self):
        from models import EdgeReportIngest

        self._set_owner(None)

        report_uuid = self._post_report().json()["uuid"]

        assert os.path.isfile(f"{self._pdf_dir}{os.sep}edge-sync-user{os.sep}{report_uuid}.pdf")
        row = self.db.query(EdgeReportIngest).one()
        assert row.user_id == "edge-sync-user"
        assert row.uploaded_by_user_id == "edge-sync-user"

    def test_an_empty_setting_is_the_same_as_none(self):
        from core.config import Settings

        empty = Settings(**{**REQUIRED_ENV_VARS, "REPORTING_EDGE_REPORT_OWNER_USER_ID": ""})
        assert empty.REPORTING_EDGE_REPORT_OWNER_USER_ID is None

        from models import EdgeReportIngest

        self._set_owner("")
        report_uuid = self._post_report().json()["uuid"]

        assert os.path.isfile(f"{self._pdf_dir}{os.sep}edge-sync-user{os.sep}{report_uuid}.pdf")
        row = self.db.query(EdgeReportIngest).one()
        assert row.user_id == "edge-sync-user"
        assert row.uploaded_by_user_id == "edge-sync-user"

    def test_duplicate_submission_with_an_owner_returns_the_existing_report_and_no_second_file(self):
        self._set_owner("owner-user")

        first_uuid = self._post_report().json()["uuid"]
        second = self._post_report()

        assert second.json() == {"uuid": first_uuid, "deduplicated": True}
        assert os.listdir(f"{self._pdf_dir}{os.sep}owner-user") == [f"{first_uuid}.pdf"]
