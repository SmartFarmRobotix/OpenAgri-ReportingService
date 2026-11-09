"""A report whose images are pushed from the edge is image-complete only
when BOTH variants came from the session's own pair, the session's index row
exists before the render is scheduled, and the pair upload reports whether
the report now carries both variants.

The image source (the GK pull) and the DB (a temp SQLite, the model's own
schema) are the doubles; the render, the endpoints and the local push store
are the real code.
"""
from __future__ import annotations

import base64
import copy
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from fastapi import BackgroundTasks, HTTPException

from tests.unit.test_pest_session_pdf_core import (
    _RS_ENV,
    _SAMPLE_PAYLOAD,
    _TINY_PNG,
    _extract_pdf_text,
)

BASE_URL = "/api/v1/openagri-report"
SESSION_ID = "pair-completion-session"
BEST_IMAGE_ID = "content-hash-shared-by-two-sessions"
CLEAN_CAPTION = re.compile(r"Best image(?! with the detected regions)")
ANNOTATED_CAPTION = "Best image with the detected regions"


class _FakeToken:
    def __init__(self, id):
        self.id = id
        self.user_id = id


def _user_login(token):
    if token == "valid":
        return _FakeToken(id="user-1")
    raise HTTPException(status_code=401, detail="Not Auth!")


class _Base(TestCase):
    def setUp(self):
        for k, v in _RS_ENV.items():
            os.environ.setdefault(k, v)

        from api.deps import get_current_user, get_db
        from core import settings
        from db.base_class import Base
        from fastapi.testclient import TestClient
        from main import app
        from models import SessionReportIndex  # noqa: F401 - registers the table
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        import utils.pest_session_report as wrapper

        self.wrapper = wrapper
        self.pdf_dir = tempfile.mkdtemp()
        self.images_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.pdf_dir, True)
        self.addCleanup(shutil.rmtree, self.images_dir, True)
        (Path(self.pdf_dir) / "user-1").mkdir()

        for name, value in (
            ("PDF_DIRECTORY", self.pdf_dir + os.sep),
            ("IMAGES_DIRECTORY", self.images_dir),
            ("REPORTING_USING_GATEKEEPER", True),
        ):
            original = getattr(settings, name, None)
            setattr(settings, name, value)
            self.addCleanup(setattr, settings, name, original)

        db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_file.close()
        self.addCleanup(os.unlink, db_file.name)
        engine = create_engine(
            f"sqlite:///{db_file.name}", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(bind=engine)
        self.addCleanup(engine.dispose)
        self.session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        patcher = patch.object(wrapper, "SessionLocal", self.session_factory)
        patcher.start()
        self.addCleanup(patcher.stop)

        def _get_db():
            db = self.session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_current_user] = _user_login
        app.dependency_overrides[get_db] = _get_db
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)

        self.requested_urls = []
        self.gk_answers = {}

        def _fake_get(url, headers=None, timeout=None):
            self.requested_urls.append(url)
            response = MagicMock()
            for suffix, (content, content_type) in self.gk_answers.items():
                if url.endswith(suffix):
                    response.status_code = 200
                    response.content = content
                    response.headers = {"Content-Type": content_type}
                    return response
            response.status_code = 404
            response.content = b""
            response.headers = {}
            return response

        patcher = patch.object(wrapper.requests, "get", _fake_get)
        patcher.start()
        self.addCleanup(patcher.stop)


    def _request_body(self, session_id=SESSION_ID, **risk_assessment):
        body = copy.deepcopy(_SAMPLE_PAYLOAD)
        body["envelope"]["session_id"] = session_id
        body["envelope"]["best_image_id"] = BEST_IMAGE_ID
        body["envelope"]["image_count"] = 1
        body["envelope"]["provenance"]["aggregation_rule_version"] = "1.0.0"
        body["contract_version"] = "1.5.0"
        if risk_assessment:
            body["risk_assessment"] = risk_assessment
        return body

    def _post_report(self, body=None):
        response = self.client.post(
            f"{BASE_URL}/pest-session-report/",
            params={"token": "valid"},
            json=body or self._request_body(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["uuid"]

    def _post_pair(self, session_id=SESSION_ID, artifact_hash="pair-hash-1"):
        b64 = base64.b64encode(_TINY_PNG).decode()
        response = self.client.post(
            f"{BASE_URL}/pest-session-images/{session_id}",
            params={"token": "valid"},
            json={
                "image_id": BEST_IMAGE_ID,
                "artifact_hash": artifact_hash,
                "clean_content_type": "image/png",
                "clean_b64": b64,
                "annotated_content_type": "image/png",
                "annotated_b64": b64,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _row(self, session_id=SESSION_ID):
        from models import SessionReportIndex

        db = self.session_factory()
        try:
            row = db.query(SessionReportIndex).filter_by(session_id=session_id).first()
            if row is None:
                return None
            return {
                "user_id": row.user_id,
                "report_uuid": row.report_uuid,
                "pdf_file_name": row.pdf_file_name,
                "payload_json": row.payload_json,
                "images_embedded": row.images_embedded,
            }
        finally:
            db.close()

    def _pdf_text(self, report_uuid):
        path = Path(self.pdf_dir) / "user-1" / f"{report_uuid}.pdf"
        self.assertTrue(path.is_file(), path)
        return " ".join(_extract_pdf_text(str(path)).split())

    def _hold_background_tasks(self):
        """Capture the scheduled render instead of running it, so a test can
        act while 'the first render has not finished'."""
        held = []
        patcher = patch.object(
            BackgroundTasks, "add_task", lambda self_, f, *a, **kw: held.append((f, a, kw))
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return held


class TestImageSourceNeverReadsBytes(_Base):
    def test_no_bytes_url_is_requested_when_the_variant_routes_fail(self):
        source = self.wrapper._make_image_source("other-session", BEST_IMAGE_ID, "tok")
        self.gk_answers = {"/bytes": (_TINY_PNG, "image/png")}
        self.assertIsNone(source("clean"))
        self.assertIsNone(source("annotated"))
        self.assertEqual(len(self.requested_urls), 2)
        self.assertTrue(self.requested_urls[0].endswith(f"/image/{BEST_IMAGE_ID}/clean"))
        self.assertTrue(self.requested_urls[1].endswith(f"/image/{BEST_IMAGE_ID}/annotated"))
        self.assertFalse([u for u in self.requested_urls if u.endswith("/bytes")])

    def test_the_local_push_store_comes_first_for_both_variants(self):
        self._post_pair()
        source = self.wrapper._make_image_source(SESSION_ID, BEST_IMAGE_ID, "tok")
        self.assertEqual(source("clean")[0], _TINY_PNG)
        self.assertEqual(source("annotated")[0], _TINY_PNG)
        self.assertEqual(self.requested_urls, [])


class TestReportIsCompletedFromTheSessionsOwnPair(_Base):
    def test_index_row_exists_before_the_render_is_scheduled(self):
        held = self._hold_background_tasks()
        report_uuid = self._post_report()

        self.assertEqual(len(held), 1)
        row = self._row()
        self.assertIsNotNone(row)
        self.assertEqual(row["report_uuid"], report_uuid)
        self.assertEqual(row["user_id"], "user-1")
        self.assertEqual(row["pdf_file_name"], f"user-1/{report_uuid}")
        self.assertFalse(row["images_embedded"])
        self.assertEqual(
            __import__("json").loads(row["payload_json"])["envelope"]["session_id"], SESSION_ID
        )

    def test_clean_only_render_leaves_the_row_not_embedded(self):
        self.gk_answers = {"/clean": (_TINY_PNG, "image/png")}
        report_uuid = self._post_report()

        self.assertFalse(self._row()["images_embedded"])
        text = self._pdf_text(report_uuid)
        self.assertRegex(text, CLEAN_CAPTION)
        self.assertNotIn(ANNOTATED_CAPTION, text)

    def test_report_is_completed_once_the_pair_later_lands(self):
        self.gk_answers = {"/clean": (_TINY_PNG, "image/png")}
        report_uuid = self._post_report()
        first_row = self._row()
        self.assertFalse(first_row["images_embedded"])

        from api.api_v1.endpoints import report as report_endpoint

        with patch.object(
            report_endpoint,
            "create_pest_session_report_pdf",
            wraps=report_endpoint.create_pest_session_report_pdf,
        ) as spy:
            answer = self._post_pair()

        self.assertTrue(answer["stored"])
        self.assertTrue(answer["report_updated"])
        self.assertTrue(answer["images_embedded"])
        spy.assert_called_once()
        self.assertEqual(spy.call_args.kwargs["pdf_file_name"], first_row["pdf_file_name"])
        self.assertEqual(
            spy.call_args.kwargs["payload"], __import__("json").loads(first_row["payload_json"])
        )
        row = self._row()
        self.assertTrue(row["images_embedded"])
        self.assertEqual(row["report_uuid"], report_uuid)
        text = self._pdf_text(report_uuid)
        self.assertRegex(text, CLEAN_CAPTION)
        self.assertIn(ANNOTATED_CAPTION, text)

        status = self.client.get(
            f"{BASE_URL}/pest-session-report/by-session/{SESSION_ID}/status",
            params={"token": "valid"},
        ).json()
        self.assertFalse(status["images_pending"])

    def test_an_image_found_elsewhere_does_not_complete_the_report(self):
        self.gk_answers = {
            "/clean": (_TINY_PNG, "image/png"),
            "/bytes": (_TINY_PNG, "image/png"),
        }
        report_uuid = self._post_report()

        self.assertFalse(self._row()["images_embedded"])
        self.assertFalse([u for u in self.requested_urls if u.endswith("/bytes")])

        answer = self._post_pair()
        self.assertTrue(answer["report_updated"])
        self.assertTrue(answer["images_embedded"])
        text = self._pdf_text(report_uuid)
        self.assertIn(ANNOTATED_CAPTION, text)
        self.assertFalse([u for u in self.requested_urls if u.endswith("/bytes")])

    def test_a_pair_that_lands_before_the_first_render_finished_still_completes_the_report(self):
        held = self._hold_background_tasks()
        report_uuid = self._post_report()
        self.assertEqual(len(held), 1)
        self.assertFalse(self._row()["images_embedded"])

        answer = self._post_pair()
        self.assertTrue(answer["report_updated"])
        self.assertTrue(answer["images_embedded"])

        func, args, kwargs = held[0]
        self.assertTrue(func(*args, **kwargs))

        self.assertTrue(self._row()["images_embedded"])
        text = self._pdf_text(report_uuid)
        self.assertRegex(text, CLEAN_CAPTION)
        self.assertIn(ANNOTATED_CAPTION, text)

    def test_a_pair_that_lands_before_any_report_is_stored_and_the_report_embeds_it(self):
        answer = self._post_pair()
        self.assertTrue(answer["stored"])
        self.assertFalse(answer["report_updated"])
        self.assertFalse(answer["images_embedded"])

        report_uuid = self._post_report()
        self.assertTrue(self._row()["images_embedded"])
        self.assertIn(ANNOTATED_CAPTION, self._pdf_text(report_uuid))

    def test_a_repeated_push_for_an_embedded_report_does_not_re_render(self):
        self._post_pair()
        self._post_report()
        self.assertTrue(self._row()["images_embedded"])

        from api.api_v1.endpoints import report as report_endpoint

        with patch.object(report_endpoint, "create_pest_session_report_pdf") as spy:
            answer = self._post_pair()
        spy.assert_not_called()
        self.assertFalse(answer["report_updated"])
        self.assertTrue(answer["images_embedded"])

    def test_renders_of_one_session_do_not_overlap(self):
        """The first render and a pair-triggered re-render can be in flight
        together; the later one must be the final one."""
        active = []
        overlaps = []
        guard = threading.Lock()

        def slow_render(payload, image_source, path):
            with guard:
                active.append(1)
                if len(active) > 1:
                    overlaps.append(True)
            time.sleep(0.05)
            with guard:
                active.pop()
            return MagicMock(images_embedded=True)

        payload = self._request_body()
        with patch.object(self.wrapper, "render_pest_session_pdf", slow_render):
            threads = [
                threading.Thread(
                    target=self.wrapper.create_pest_session_report_pdf,
                    kwargs={"payload": payload, "token": "t", "pdf_file_name": "user-1/u"},
                )
                for _ in range(2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(overlaps, [])


class TestReportWithoutThePairAfterExhaustedRetries(_Base):
    """The edge gave up pushing: the report exists without the pair and the
    session stays pending. Content branches by designation."""

    def _assert_valid_pdf_without_the_pair(self, report_uuid):
        import pypdf

        path = Path(self.pdf_dir) / "user-1" / f"{report_uuid}.pdf"
        self.assertGreaterEqual(len(pypdf.PdfReader(str(path)).pages), 1)
        text = self._pdf_text(report_uuid)
        self.assertNotRegex(text, CLEAN_CAPTION)
        self.assertNotIn(ANNOTATED_CAPTION, text)
        self.assertTrue(self._row()["images_embedded"] is False)
        return text

    def test_fine_tuned_session_report_completes_without_the_pair(self):
        body = self._request_body(
            level="High",
            details="Conclusion: treat the infestation. Recommended action: spray.",
        )
        text = self._assert_valid_pdf_without_the_pair(self._post_report(body))
        self.assertIn("Conclusion: treat the infestation.", text)
        self.assertIn("Recommended action: spray.", text)

    def test_stock_model_session_report_defers_the_pair_without_agricultural_advice(self):
        body = self._request_body(
            level="Low",
            details=(
                "Note: detections in this report were produced by a generic integration "
                "model not designated for agricultural claims (test-detector version 1). "
                "Agricultural conclusions and recommendations are suppressed for this report."
            ),
        )
        text = self._assert_valid_pdf_without_the_pair(self._post_report(body))
        self.assertIn("not designated for agricultural claims", text)
        self.assertNotIn("Conclusion: ", text)
        self.assertNotIn("Recommended action: ", text)


class TestByStatusRouteCoversEdgeSyncedReports(_Base):
    """A session whose report
    was synced from an edge node has a status, and the answer names where the
    report came from."""

    EDGE_SESSION = "edge-synced-session"

    def _add_edge_ingest(self, report_uuid, created_at, session_id=EDGE_SESSION, content_hash=None):
        from models import EdgeReportIngest

        db = self.session_factory()
        try:
            db.add(
                EdgeReportIngest(
                    session_id=session_id,
                    content_hash=content_hash or f"hash-{report_uuid}",
                    report_uuid=report_uuid,
                    pdf_file_name=f"user-1/{report_uuid}",
                    user_id="user-1",
                    uploaded_by_user_id="user-1",
                    created_at=created_at,
                )
            )
            db.commit()
        finally:
            db.close()

    def _status(self, session_id):
        return self.client.get(
            f"{BASE_URL}/pest-session-report/by-session/{session_id}/status",
            params={"token": "valid"},
        )

    def test_an_edge_synced_session_has_a_status(self):
        import datetime

        self._add_edge_ingest("edge-report-1", datetime.datetime(2026, 9, 24, 8, 0, 0))

        response = self._status(self.EDGE_SESSION)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {
                "session_id": self.EDGE_SESSION,
                "report_uuid": "edge-report-1",
                "images_pending": False,
                "origin": "edge",
            },
        )

    def test_a_cloud_session_reports_its_origin_and_keeps_its_fields(self):
        report_uuid = self._post_report()

        body = self._status(SESSION_ID).json()

        self.assertEqual(body["origin"], "cloud")
        self.assertEqual(body["report_uuid"], report_uuid)
        self.assertEqual(body["session_id"], SESSION_ID)
        self.assertTrue(body["images_pending"])

    def test_a_cloud_row_answers_before_an_edge_ingest_of_the_same_session(self):
        import datetime

        report_uuid = self._post_report()
        self._add_edge_ingest("edge-report-2", datetime.datetime(2026, 9, 24, 8, 0, 0), session_id=SESSION_ID)

        body = self._status(SESSION_ID).json()

        self.assertEqual(body["origin"], "cloud")
        self.assertEqual(body["report_uuid"], report_uuid)

    def test_an_unknown_session_is_still_404(self):
        self.assertEqual(self._status("no-such-session").status_code, 404)

    def test_two_edge_ingests_of_one_session_answer_with_the_later_one(self):
        import datetime

        self._add_edge_ingest("edge-report-later", datetime.datetime(2026, 9, 24, 9, 0, 0))
        self._add_edge_ingest("edge-report-earlier", datetime.datetime(2026, 9, 24, 8, 0, 0))

        response = self._status(self.EDGE_SESSION)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["report_uuid"], "edge-report-later")
