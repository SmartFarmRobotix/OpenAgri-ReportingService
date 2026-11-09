"""Cloud RS still renders its report through the extracted component:
  TestCloudWrapperRendersThroughCore
  drives the real `create_pest_session_report_pdf` (the thin RS wrapper)
  end-to-end and asserts the output came from `render_pest_session_pdf`
  (pest_session_pdf_core.py), not a second/forked implementation.

  A report generated with host/container networking disabled completes and
  renders the logo on every page, with zero outbound connection attempts
  recorded by the connection audit:
  TestZeroNetworkLogoRendering drives `render_pest_session_pdf` directly,
  WITHOUT patching `EX.header` away (unlike the pre-existing determinism
  tests) -- this is what actually exercises the local logo-rendering code path,
  with the connection audit installed to prove it.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import patch

_PDFTOTEXT = shutil.which("pdftotext")

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

_APP_DIR = Path(__file__).resolve().parents[2]

_SAMPLE_PAYLOAD = {
    "envelope": {
        "session_id": "pdf-core-test-session",
        "best_image_id": "img-1",
        "time_start": "2026-01-01T00:00:00Z",
        "time_end": "2026-01-01T01:00:00Z",
        "latitude": 45.0,
        "longitude": 10.0,
        "provenance": {
            "detector_impl_id": "lookup-detector",
            "detector_version": "1.0.0",
            "designation_status": "stub",
            "target_class_set": ["brassica_seedling"],
            "confidence_floor": 0.3,
        },
    },
    "infestation_score": 0.55,
    "risk_assessment": {"level": "Medium", "details": "test"},
    "pest_crop_context": None,
    "risk_reasoning": {"nn_score": 0.55, "nn_verdict": "Moderate", "nn_note": "test"},
    "weather_summary": None,
    "forecast_outlook": None,
}


class TestCloudWrapperRendersThroughCore(TestCase):
    """The thin RS wrapper must still produce a real, valid PDF
    proving `create_pest_session_report_pdf` actually delegates to the
    extracted `render_pest_session_pdf`, not a parallel implementation."""

    def setUp(self):
        import models
        from db.session import engine

        models.User.metadata.create_all(bind=engine)

    def tearDown(self):
        import models
        from db.session import engine

        models.User.metadata.drop_all(bind=engine)
        engine.dispose()
        db_path = Path.cwd() / "test.db"
        if db_path.exists():
            db_path.unlink()

    def test_cloud_wrapper_produces_valid_pdf_via_core(self):
        import utils.pest_session_report as wrapper

        with tempfile.TemporaryDirectory() as tmp:
            with patch("utils.pest_session_report.settings") as mock_settings:
                mock_settings.PDF_DIRECTORY = tmp + "/"
                mock_settings.REPORTING_USING_GATEKEEPER = False
                mock_settings.REPORTING_GATEKEEPER_BASE_URL = "http://gatekeeper:8000"
                mock_settings.REPORTING_INFERENCE_SERVICE_NAME = "inference"

                with patch.object(
                    wrapper, "render_pest_session_pdf", wraps=wrapper.render_pest_session_pdf
                ) as spy:
                    wrapper.create_pest_session_report_pdf(
                        payload=_SAMPLE_PAYLOAD,
                        token="test-token",
                        pdf_file_name="core_wrapper_test",
                    )
                    spy.assert_called_once()

            pdf_bytes = (Path(tmp) / "core_wrapper_test.pdf").read_bytes()
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 0)


class TestZeroNetworkLogoRendering(TestCase):
    """EX.header is NOT patched away in this test class
    unlike the pre-existing determinism tests -- specifically so the real
    (now-local) logo rendering code path runs and is audited."""

    def setUp(self):
        from core import connection_audit

        connection_audit._reset_for_tests()
        self._connection_audit = connection_audit

    def tearDown(self):
        self._connection_audit._reset_for_tests()

    def test_report_with_networking_disabled_renders_logo_with_zero_connections(self):
        from utils.pest_session_pdf_core import render_pest_session_pdf

        def local_only_image_source(variant: str):
            return None

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"CONN_AUDIT_LOG_DIR": tmp}):
                self._connection_audit.install("reporting-test")
                output_path = str(Path(tmp) / "zero_network_test.pdf")
                result = render_pest_session_pdf(_SAMPLE_PAYLOAD, local_only_image_source, output_path)

            pdf_bytes = Path(output_path).read_bytes()

        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 0)
        self.assertFalse(result.images_embedded)
        self.assertEqual(
            self._connection_audit.attempt_count(),
            0,
            "report generation (including the per-page logo) must make zero "
            "outbound connection attempts",
        )

    def test_logo_asset_is_local_not_remote(self):
        """The bundled asset file must exist on disk -- the whole point of
        the bundled-logo change is that there is no remote URL to fall back on."""
        from utils.pest_session_pdf_core import _ASSETS_DIR

        logo_path = Path(_ASSETS_DIR) / "openagri_logo.png"
        self.assertTrue(logo_path.is_file(), f"expected a bundled logo asset at {logo_path}")

    def test_no_remote_url_referenced_in_source(self):
        import utils.pest_session_pdf_core as core

        source = Path(core.__file__).read_text()
        self.assertNotIn("http://", source)
        self.assertNotIn("https://", source)


_OFFICIAL_LOGO_SHA256 = "ecf04b07fa49822333e4bb68310d0b5d1f36fdea66e338e703c474fd1137fe45"

_TINY_PNG = __import__("base64").b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _extract_pdf_text(pdf_path: str) -> str:
    import pypdf

    reader = pypdf.PdfReader(pdf_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@skipUnless(_PDFTOTEXT, "pdftotext (poppler-utils) is not installed")
class TestPolishedPresentation(TestCase):
    """Human-readable windows, site
    label / honest position absence, RGB caption, engine-provenance block,
    weather unavailability sentence — asserted on extracted PDF text
    through the production render path. Whitespace-normalized assertions
    guard against extractor spacing artifacts."""

    def _render(self, payload, with_image=False):
        from utils.pest_session_pdf_core import render_pest_session_pdf

        def image_source(variant: str):
            if with_image and variant == "clean":
                return (_TINY_PNG, "image/png")
            return None

        with tempfile.TemporaryDirectory() as tmp:
            output_path = str(Path(tmp) / "polish_test.pdf")
            render_pest_session_pdf(payload, image_source, output_path)
            raw = _extract_pdf_text(output_path)
        return raw, "".join(raw.split())

    def _payload(self, **overrides):
        import copy

        payload = copy.deepcopy(_SAMPLE_PAYLOAD)
        payload["envelope"]["time_start"] = "2026-02-13T07:23:26.820000+00:00"
        payload["envelope"]["time_end"] = "2026-02-13T07:57:49.040000+00:00"
        payload.update(overrides)
        return payload

    def test_pest_line_carries_the_scientific_name_when_present(self):
        context = {
            "pest_name": "Cabbage aphid",
            "pest_scientific_name": "Brevicoryne brassicae",
            "pest_eppo_code": "BRVCBR",
        }
        _, squeezed = self._render(self._payload(pest_crop_context=context))
        self.assertIn("Cabbageaphid(Brevicorynebrassicae)", squeezed)
        self.assertIn("BRVCBR", squeezed)

    def test_pest_line_is_the_common_name_alone_without_a_scientific_name(self):
        context = {"pest_name": "Cabbage aphid", "pest_eppo_code": "BRVCBR"}
        raw, squeezed = self._render(self._payload(pest_crop_context=context))
        self.assertIn("Cabbageaphid", squeezed)
        self.assertNotIn("Cabbageaphid(", squeezed)
        self.assertNotIn("Brevicoryne", raw)

    def test_window_renders_human_readable(self):
        raw, squeezed = self._render(self._payload())
        self.assertIn("13Feb2026,07:23-07:57UTC", squeezed)
        self.assertNotIn("2026-02-13T07:23:26.820000", raw)
        self.assertNotIn("2026-02-13T07:57:49.040000", raw)

    def test_cross_day_window_keeps_both_dates(self):
        payload = self._payload()
        payload["envelope"]["time_end"] = "2026-02-14T06:10:00+00:00"
        _, squeezed = self._render(payload)
        self.assertIn("13Feb202607:23UTC-14Feb202606:10UTC", squeezed)

    def test_unparseable_window_falls_back_to_raw(self):
        payload = self._payload()
        payload["envelope"]["time_start"] = "not-a-timestamp"
        raw, _ = self._render(payload)
        self.assertIn("not-a-timestamp", raw)

    def test_one_malformed_side_still_formats_the_other(self):
        payload = self._payload()
        payload["envelope"]["time_start"] = "not-a-timestamp"
        raw, squeezed = self._render(payload)
        self.assertIn("13Feb202607:57UTC", squeezed)
        self.assertNotIn("2026-02-13T07:57:49.040000", raw)

    def test_weather_window_row_formats_the_composer_shape(self):
        payload = self._payload()
        payload["risk_reasoning"] = dict(payload["risk_reasoning"])
        payload["risk_reasoning"]["weather_window"] = (
            "2026-02-13T07:23:26.820000+00:00  →  2026-02-13T07:57:49.040000+00:00"
        )
        raw, squeezed = self._render(payload)
        self.assertIn("WeatherWindow13Feb2026,07:23-07:57UTC", squeezed)
        self.assertNotIn("2026-02-13T07:23:26.820000", raw)
        self.assertNotIn("2026-02-13T07:57:49.040000", raw)

    def test_weather_data_wins_over_contradictory_reason(self):
        payload = self._payload(
            weather_summary={
                "source": "open-meteo (history)",
                "observed": {"temperature_2m_max_C": 4.1},
                "unavailable_reason": "should never render",
            }
        )
        raw, _ = self._render(payload)
        self.assertIn("Temp max: 4.1 C", raw)
        self.assertNotIn("Weather data unavailable", raw)
        self.assertNotIn("should never render", raw)

    def test_location_with_site_label(self):
        raw, squeezed = self._render(self._payload(site_label="Field site, region"))
        self.assertIn("Fieldsite,region", squeezed)
        self.assertIn("45.0", raw)
        self.assertIn("10.0", raw)

    def test_location_without_site_label_keeps_coordinates(self):
        raw, squeezed = self._render(self._payload())
        self.assertIn("Location", raw)
        self.assertIn("45.0", raw)
        self.assertIn("10.0", raw)
        self.assertNotIn("Location(lat,lon)", squeezed)

    def test_location_position_unavailable(self):
        payload = self._payload()
        payload["envelope"].pop("latitude")
        payload["envelope"].pop("longitude")
        raw, _ = self._render(payload)
        self.assertIn("Position unavailable for this session", raw)

    def _render_variants(self, *, clean=True, annotated=True):
        from utils.pest_session_pdf_core import render_pest_session_pdf

        def image_source(variant: str):
            available = clean if variant == "clean" else annotated
            return (_TINY_PNG, "image/png") if available else None

        with tempfile.TemporaryDirectory() as tmp:
            output_path = str(Path(tmp) / "variants_test.pdf")
            result = render_pest_session_pdf(self._payload(), image_source, output_path)
            raw = _extract_pdf_text(output_path)
        return result, " ".join(raw.split())

    def test_both_variants_render_with_their_captions_once_each_in_order(self):
        result, text = self._render_variants()
        self.assertEqual(text.count("Session image"), 1)
        self.assertEqual(text.count("Best image with the detected regions"), 1)
        self.assertEqual(len(_CLEAN_CAPTION.findall(text)), 1)
        positions = [
            text.index("Session image"),
            _CLEAN_CAPTION.search(text).start(),
            text.index("Best image with the detected regions"),
        ]
        self.assertEqual(positions, sorted(positions))
        for old in (
            "Session Images",
            "RGB capture",
            "Detection Overlay (bounding boxes)",
            "Second capture",
            "Session images unavailable",
        ):
            self.assertNotIn(old, text)
        self.assertTrue(result.images_embedded)

    def test_missing_annotated_variant_renders_only_the_first_caption(self):
        result, text = self._render_variants(annotated=False)
        self.assertEqual(len(_CLEAN_CAPTION.findall(text)), 1)
        self.assertNotIn("Best image with the detected regions", text)
        self.assertFalse(result.images_embedded)

    def test_missing_clean_variant_renders_only_the_annotated_caption(self):
        result, text = self._render_variants(clean=False)
        self.assertEqual(text.count("Best image with the detected regions"), 1)
        self.assertIsNone(_CLEAN_CAPTION.search(text))
        self.assertFalse(result.images_embedded)

    def test_no_variant_says_so_and_reports_not_embedded(self):
        result, text = self._render_variants(clean=False, annotated=False)
        self.assertIn("Session images unavailable; report generated without images.", text)
        self.assertNotIn("Best image", text)
        self.assertFalse(result.images_embedded)

    def test_a_variant_that_cannot_be_decoded_is_not_counted_as_embedded(self):
        from utils.pest_session_pdf_core import render_pest_session_pdf

        def image_source(variant: str):
            return (_TINY_PNG, "image/png") if variant == "clean" else (b"not an image", "image/png")

        with tempfile.TemporaryDirectory() as tmp:
            result = render_pest_session_pdf(self._payload(), image_source, str(Path(tmp) / "bad.pdf"))
        self.assertFalse(result.images_embedded)

    def test_engine_provenance_block_renders_when_present(self):
        payload = self._payload()
        payload["risk_reasoning"] = dict(payload["risk_reasoning"])
        payload["risk_reasoning"]["engine_provenance"] = [
            "authoritative path: pdm-rule-engine v0.2.0, fusion v1-max-of-bands",
            "comparative path: fuzzy-mamdani-risk v2.0.0, fusion v1-max-of-bands",
        ]
        raw, _ = self._render(payload)
        self.assertIn("Engine provenance:", raw)
        self.assertIn("pdm-rule-engine v0.2.0", raw)
        self.assertIn("fuzzy-mamdani-risk v2.0.0", raw)
        self.assertIn("v1-max-of-bands", raw)

    def test_engine_provenance_malformed_shapes_never_crash(self):
        for bad in (7, "a bare string", {"engine": "x"}):
            payload = self._payload()
            payload["risk_reasoning"] = dict(payload["risk_reasoning"])
            payload["risk_reasoning"]["engine_provenance"] = bad
            raw, _ = self._render(payload)
            self.assertNotIn("Engine provenance:", raw)

    def test_site_label_malformed_type_ignored(self):
        raw, squeezed = self._render(self._payload(site_label={"a": 1}))
        self.assertIn("45.0", raw)
        self.assertNotIn("{'a'", raw)

    def test_engine_provenance_absent_for_old_payloads(self):
        raw, _ = self._render(self._payload())
        self.assertNotIn("Engine provenance:", raw)

    def test_weather_unavailable_reason_sentence(self):
        payload = self._payload(
            weather_summary={"unavailable_reason": "position unavailable, no weather lookup attempted"}
        )
        raw, _ = self._render(payload)
        self.assertIn("Weather data unavailable for this session:", raw)
        self.assertIn("position unavailable", raw)
        self.assertNotIn("No weather summary provided", raw)

    def test_populated_weather_still_renders(self):
        payload = self._payload(
            weather_summary={
                "source": "open-meteo (history)",
                "variables": ["temperature_2m", "relative_humidity_2m"],
                "observed": {
                    "temperature_2m_max_C": 4.1,
                    "temperature_2m_min_C": -1.3,
                    "relative_humidity_2m_avg_pct": 81,
                },
            }
        )
        raw, _ = self._render(payload)
        self.assertIn("Temp max: 4.1 C", raw)
        self.assertIn("Temp min: -1.3 C", raw)
        self.assertIn("Humidity avg: 81 %", raw)
        self.assertIn("open-meteo (history)", raw)
        self.assertNotIn("No weather summary provided", raw)

    def test_allowlist_line_labeled_legibly(self):
        raw, _ = self._render(self._payload())
        self.assertIn("Scoring allowlist", raw)
        self.assertIn("brassica_seedling", raw)
        self.assertNotIn("Target classes", raw)

    def test_allowlist_renders_payload_value_verbatim(self):
        import copy

        payload = self._payload()
        payload["envelope"] = copy.deepcopy(payload["envelope"])
        payload["envelope"]["provenance"]["target_class_set"] = ["aphid", "x-test-class"]
        raw, _ = self._render(payload)
        self.assertIn("aphid, x-test-class", raw)
        self.assertNotIn("brassica_seedling", raw)

    def test_logo_is_the_official_asset(self):
        import hashlib

        from utils.pest_session_pdf_core import _ASSETS_DIR

        logo_bytes = (Path(_ASSETS_DIR) / "openagri_logo.png").read_bytes()
        self.assertEqual(hashlib.sha256(logo_bytes).hexdigest(), _OFFICIAL_LOGO_SHA256)


    _SHORT = "Weather conditions: Medium risk. Detections: Medium. Overall: Medium."
    _FULL = (
        "Weather rules for Cabbage aphid over the session window: 'temperature at least 20 \u00b0C "
        "and relative humidity at least 70 %' held for 6 of 24 hours (Medium risk). These rules "
        "put the weather risk at Medium. The window had an average temperature of 21.3 \u00b0C "
        "(maximum 27.8 \u00b0C) and an average relative humidity of 71.0 %. The detections put "
        "the session in the Medium band; the detector's confidence on the most affected images "
        "is 0.45. The risk level is the higher of the two: Medium."
    )

    def _layout(self, payload):
        import subprocess

        from utils.pest_session_pdf_core import render_pest_session_pdf

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "details_test.pdf")
            render_pest_session_pdf(payload, lambda variant: None, path)
            out = subprocess.run(
                ["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True
            ).stdout
        return [" ".join(line.split()) for line in out.splitlines()]

    def test_the_short_line_sits_in_the_risk_assessment_block(self):
        payload = self._payload(
            risk_assessment={
                "level": "Medium", "details": self._SHORT, "assessment_details_full": self._FULL,
            }
        )
        lines = self._layout(payload)

        short_at = lines.index(f"Details {self._SHORT}")
        self.assertLess(lines.index("Risk Assessment"), short_at)
        self.assertLess(short_at, lines.index("Pest & Crop Context"))

    def test_the_full_paragraph_sits_under_assessment_details_after_the_session_image(self):
        payload = self._payload(
            risk_assessment={
                "level": "Medium", "details": self._SHORT, "assessment_details_full": self._FULL,
            }
        )
        lines = self._layout(payload)

        image_at = lines.index("Session image")
        inference_at = lines.index("Inference Details")
        heading_at = lines.index("Assessment details")
        self.assertLess(image_at, inference_at)
        self.assertLess(inference_at, heading_at)
        self.assertIn(self._FULL, " ".join(lines[heading_at + 1:]))
        self.assertEqual(" ".join(lines).count("held for 6 of 24 hours"), 1)
        self.assertFalse([line for line in lines[:image_at] if "held for" in line])

    def test_the_assessment_details_come_before_the_engine_provenance_lines(self):
        payload = self._payload(
            risk_assessment={"level": "Medium", "details": self._SHORT, "assessment_details_full": self._FULL},
            risk_reasoning={
                "nn_score": 0.55, "nn_verdict": "Moderate", "nn_note": "x",
                "engine_provenance": ["authoritative path: pdm-rule-engine 1.4.0, fusion v1"],
            },
        )
        lines = self._layout(payload)

        self.assertLess(
            lines.index("Assessment details"),
            lines.index("authoritative path: pdm-rule-engine 1.4.0, fusion v1"),
        )

    def test_a_degraded_report_says_score_only_on_top_and_names_the_reason_below(self):
        full = (
            "Score-only assessment: the weather was not evaluated (weather unavailable). "
            "The level comes from the infestation score (0.62) alone."
        )
        payload = self._payload(
            risk_assessment={
                "level": "Medium",
                "details": "Based on the detections in this session; no weather data for this assessment.",
                "assessment_details_full": full,
            }
        )
        lines = self._layout(payload)

        self.assertIn(
            "Details Based on the detections in this session; no weather data for this assessment.", lines
        )
        heading_at = lines.index("Assessment details")
        self.assertIn(full, " ".join(lines[heading_at + 1:]))
        for fact in ("temperature at least", "held for", "21.3"):
            self.assertNotIn(fact, " ".join(lines))

    def test_without_the_full_paragraph_no_assessment_details_line_is_printed(self):
        lines = self._layout(self._payload(risk_assessment={"level": "Medium", "details": self._SHORT}))

        self.assertNotIn("Assessment details", lines)

    def test_a_malformed_full_paragraph_is_ignored(self):
        lines = self._layout(
            self._payload(
                risk_assessment={"level": "Medium", "details": self._SHORT, "assessment_details_full": ["a", "b"]}
            )
        )

        self.assertNotIn("Assessment details", lines)

    def test_no_raw_variable_identifier_reaches_the_extracted_text(self):
        payload = self._payload(
            weather_summary={
                "source": "open-meteo (history)",
                "variables": ["temperature_2m", "relative_humidity_2m", "precipitation", "soil_moisture_0_to_1cm"],
                "observed": {"temperature_2m_max_C": 4.1, "relative_humidity_2m_avg_pct": 81},
            },
            risk_assessment={
                "level": "Medium", "details": self._SHORT, "assessment_details_full": self._FULL,
            },
        )
        lines = self._layout(payload)

        self.assertIn(
            "Variables temperature, relative humidity, precipitation, soil_moisture_0_to_1cm", lines
        )
        whole = " ".join(lines)
        for identifier in ("temperature_2m", "relative_humidity_2m"):
            self.assertNotIn(identifier, whole)

    _FUZZY = (
        "Fuzzy model (Mamdani) over the same window: daily average temperature 5.6 °C, "
        "daily maximum relative humidity 92.0 %, 1.2 mm of rain in total. Humid days in a row "
        "(relative humidity of at least 80 %): 1 of the 2 the model requires. Leaf wetness: "
        "18.0 hours on the wettest day, 24.0 required for High. 1 of the 1 days had values "
        "within the ranges of at least one of the model's 3 rules (Low). The model's daily "
        "score is 0.0 of 100; the resulting band is Low."
    )

    def test_both_labeled_sub_blocks_render_under_assessment_details(self):
        payload = self._payload(
            risk_assessment={
                "level": "Medium", "details": self._SHORT,
                "assessment_details_full": self._FULL, "fuzzy_details": self._FUZZY,
            }
        )
        lines = self._layout(payload)

        heading_at = lines.index("Assessment details")
        weather_at = lines.index("Weather rules (PDM)")
        fuzzy_at = lines.index("Fuzzy model (Mamdani)")
        self.assertLess(heading_at, weather_at)
        self.assertLess(weather_at, fuzzy_at)
        tail = " ".join(lines[heading_at + 1:])
        self.assertIn(self._FULL, tail)
        self.assertIn(self._FUZZY, tail)

    def test_no_fuzzy_details_prints_no_labels_the_paragraph_as_today(self):
        payload = self._payload(
            risk_assessment={
                "level": "Medium", "details": self._SHORT, "assessment_details_full": self._FULL,
            }
        )
        lines = self._layout(payload)

        self.assertIn("Assessment details", lines)
        self.assertNotIn("Weather rules (PDM)", lines)
        self.assertNotIn("Fuzzy model (Mamdani)", lines)
        heading_at = lines.index("Assessment details")
        self.assertIn(self._FULL, " ".join(lines[heading_at + 1:]))

    def test_a_malformed_fuzzy_paragraph_is_ignored(self):
        payload = self._payload(
            risk_assessment={
                "level": "Medium", "details": self._SHORT,
                "assessment_details_full": self._FULL, "fuzzy_details": ["a", "b"],
            }
        )
        lines = self._layout(payload)

        self.assertIn("Assessment details", lines)
        self.assertNotIn("Weather rules (PDM)", lines)
        self.assertNotIn("Fuzzy model (Mamdani)", lines)


@skipUnless(_PDFTOTEXT, "pdftotext (poppler-utils) is not installed")
class TestTwoCellRowExtraction(TestCase):
    """The extraction probe: a
    two-cell row whose label cell is the LONGEST pinned verdict prefix must
    extract, on whitespace-normalised `pdftotext -layout` text, as the
    prefix followed by its band on ONE line. The layout change moves the
    verdict sentences into such rows; this test proves the assertion
    contract survives it."""

    PREFIX = "Edge assessment (score band): "

    def _layout_text(self, draw):
        import subprocess

        from utils.pest_session_pdf_core import EX, add_fonts, _render_section_header

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "row_probe.pdf")
            pdf = EX()
            add_fonts(pdf)
            pdf.add_page()
            _render_section_header(pdf, "Risk Reasoning")
            draw(pdf)
            pdf.output(path)
            out = subprocess.run(
                ["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True
            ).stdout
        return out

    def test_the_longest_pinned_prefix_is_followed_by_its_band_on_one_line(self):
        from utils.pest_session_pdf_core import _label_column_width, _render_key_value

        labels = [self.PREFIX, "Weather-rule assessment: ", "Fuzzy assessment: "]

        def draw(pdf):
            width = _label_column_width(pdf, labels)
            for label in labels:
                _render_key_value(pdf, label, "High", label_width=width)

        raw = self._layout_text(draw)
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        self.assertIn(self.PREFIX.strip() + " High", lines, raw)
        self.assertIn("Weather-rule assessment: High", lines, raw)
        self.assertIn("Fuzzy assessment: High", lines, raw)


@skipUnless(_PDFTOTEXT, "pdftotext (poppler-utils) is not installed")
class TestSectionOrderAndVerdictRows(TestCase):
    """The section order ("Sections are ordered for the farmer") and the
    reasoning rows ("Risk reasoning renders as sentences that ARE the defined
    markers"), on `pdftotext -layout` text with each line's
    whitespace collapsed."""

    HEADINGS = (
        "Session Overview",
        "Risk Assessment",
        "Pest & Crop Context",
        "Risk Reasoning",
        "Weather Summary",
        "Forecast risk outlook: the next 5 days",
        "Session image",
        "Inference Details",
    )

    def _payload(self, note_lines, engine_provenance):
        import copy

        payload = copy.deepcopy(_SAMPLE_PAYLOAD)
        payload["risk_reasoning"] = {
            "nn_score": 0.55,
            "nn_verdict": "Moderate",
            "nn_note": "\n".join(note_lines),
            "engine_provenance": engine_provenance,
        }
        payload["pest_crop_context"] = {
            "pest_name": "Cabbage aphid",
            "pest_scientific_name": "Brevicoryne brassicae",
            "pest_eppo_code": "BRVCBR",
            "crop_name": "Cabbage / Brassica crops (Brassica oleracea)",
        }
        payload["forecast_outlook"] = {"recommendation": "Recommended action: monitor the crop."}
        return payload

    def _layout_lines(self, payload):
        import subprocess

        from utils.pest_session_pdf_core import render_pest_session_pdf

        def image_source(variant):
            return (_TINY_PNG, "image/png")

        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "order_test.pdf")
            render_pest_session_pdf(payload, image_source, path)
            out = subprocess.run(
                ["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True
            ).stdout
        return [" ".join(line.split()) for line in out.splitlines()]

    def test_cloud_report_sections_are_in_the_farmers_order(self):
        lines = self._layout_lines(
            self._payload(
                [
                    "Weather-rule assessment: High",
                    "Fuzzy assessment: High",
                ],
                [
                    "authoritative path: pdm-rule-engine 1.4.0, fusion v1",
                    "comparative path: fuzzy-mamdani 2.0.0, fusion v1",
                ],
            )
        )
        positions = [lines.index(heading) for heading in self.HEADINGS]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(set(positions)), len(positions))

        self.assertIn("Weather-rule assessment: High", lines)
        self.assertIn("Fuzzy assessment: High", lines)
        self.assertLess(
            lines.index("Weather-rule assessment: High"),
            lines.index("Fuzzy assessment: High"),
        )
        whole = " ".join(lines)
        for former in _FORMER_VERDICT_PREFIXES:
            self.assertNotIn(former, whole)

        image_at = lines.index("Session image")
        for provenance_line in (
            "authoritative path: pdm-rule-engine 1.4.0, fusion v1",
            "comparative path: fuzzy-mamdani 2.0.0, fusion v1",
        ):
            self.assertGreater(lines.index(provenance_line), lines.index("Inference Details"))
            self.assertGreater(lines.index(provenance_line), image_at)
        self.assertGreater(lines.index("Engine provenance:"), lines.index("Inference Details"))

    def test_inference_details_is_the_last_section(self):
        lines = self._layout_lines(
            self._payload(["Edge assessment (score band): High"], ["authoritative path: edge-score-band v1"])
        )
        last = lines.index("Inference Details")
        for heading in self.HEADINGS[:-1]:
            self.assertLess(lines.index(heading), last)
        body = " ".join(lines[last + 1:])
        self.assertIn("Detector", body)
        self.assertIn("authoritative path: edge-score-band v1", body)
        for heading in self.HEADINGS[:-1]:
            self.assertNotIn(heading, lines[last + 1:])

    def test_the_edge_verdict_is_a_single_row_with_one_provenance_line(self):
        lines = self._layout_lines(
            self._payload(["Edge assessment (score band): Medium"], ["authoritative path: edge-score-band v1"])
        )
        self.assertIn("Edge assessment (score band): Medium", lines)
        self.assertFalse([line for line in lines if line.startswith("Fuzzy assessment")])
        for former in _FORMER_VERDICT_PREFIXES:
            self.assertNotIn(former, " ".join(lines))
        self.assertEqual(
            [line for line in lines if line.startswith("authoritative path:")],
            ["authoritative path: edge-score-band v1"],
        )
        self.assertFalse([line for line in lines if line.startswith("comparative path:")])

    def test_a_note_that_is_not_a_labelled_sentence_stays_plain_text(self):
        lines = self._layout_lines(self._payload(["free text without a label"], None))
        self.assertIn("free text without a label", lines)


@skipUnless(_PDFTOTEXT, "pdftotext (poppler-utils) is not installed")
class TestPestAndCropContextUnavailable(TestCase):
    """A session that named a pest the
    catalogue has no record for renders the producer's one sentence section instead of the generic absence line."""

    REASON = (
        "No catalogue record exists for MYZUPE on Cabbage / Brassica crops "
        "(Brassica oleracea); the pest and crop context is unavailable for this session."
    )

    def _lines(self, **overrides):
        import copy
        import subprocess

        from utils.pest_session_pdf_core import render_pest_session_pdf

        payload = copy.deepcopy(_SAMPLE_PAYLOAD)
        payload.update(overrides)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "context_test.pdf")
            render_pest_session_pdf(payload, lambda variant: None, path)
            out = subprocess.run(
                ["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True
            ).stdout
        return " ".join(out.split())

    def test_the_reason_renders_as_one_sentence_in_the_section(self):
        text = self._lines(pest_crop_context=None, pest_crop_context_unavailable_reason=self.REASON)
        section = text[text.index("Pest & Crop Context"):text.index("Risk Reasoning")]
        self.assertIn(self.REASON, section)
        self.assertNotIn("No pest/crop context provided.", text)

    def test_without_a_reason_the_generic_line_stays(self):
        text = self._lines(pest_crop_context=None)
        self.assertIn("No pest/crop context provided.", text)

    def test_a_resolved_context_wins_over_a_reason(self):
        context = {"pest_name": "Cabbage aphid", "pest_scientific_name": "Brevicoryne brassicae", "pest_eppo_code": "BRVCBR"}
        text = self._lines(pest_crop_context=context, pest_crop_context_unavailable_reason=self.REASON)
        self.assertIn("Cabbage aphid (Brevicoryne brassicae)", text)
        self.assertNotIn(self.REASON, text)

    def test_a_malformed_reason_is_ignored(self):
        text = self._lines(pest_crop_context=None, pest_crop_context_unavailable_reason=["not", "a", "string"])
        self.assertIn("No pest/crop context provided.", text)


class TestResidualCosmetics(TestCase):
    """Presentation details: the detector line spells `version`, an empty forecast
    outlook renders no header-only table, and the composed
    recommendation sentence renders bare, exactly once."""

    def _render(self, payload):
        from utils.pest_session_pdf_core import render_pest_session_pdf

        with tempfile.TemporaryDirectory() as tmp:
            output_path = str(Path(tmp) / "residuals_test.pdf")
            render_pest_session_pdf(payload, lambda variant: None, output_path)
            raw = _extract_pdf_text(output_path)
        return raw, "".join(raw.split())

    def _payload(self, **overrides):
        import copy

        payload = copy.deepcopy(_SAMPLE_PAYLOAD)
        payload.update(overrides)
        return payload

    def test_detector_line_spells_version(self):
        raw, squeezed = self._render(self._payload())
        self.assertIn("lookup-detectorversion1.0.0", squeezed)
        self.assertNotIn("lookup-detectorv1.0.0", squeezed)

    def test_zero_day_outlook_renders_no_table_but_keeps_recommendation(self):
        payload = self._payload(
            forecast_outlook={
                "recommendation": "Recommended action: monitor the crop."
            }
        )
        raw, _ = self._render(payload)
        self.assertNotIn("Max °C", raw)
        self.assertNotIn("Precip mm", raw)
        self.assertIn("Recommended action: monitor the crop.", raw)

    def test_populated_outlook_still_renders_its_table(self):
        payload = self._payload(
            forecast_outlook={
                "days": [
                    {
                        "date": "2026-02-14",
                        "temp_max_C": 8.1,
                        "temp_min_C": 1.2,
                        "humidity_pct": 84,
                        "precipitation_mm": 0.4,
                        "wind_speed_ms": 3.2,
                        "risk_level": "Low",
                    }
                ],
                "recommendation": "Recommended action: monitor the crop.",
            }
        )
        raw, _ = self._render(payload)
        self.assertIn("Max °C", raw)
        self.assertIn("2026-02-14", raw)

    def test_recommendation_renders_bare_and_exactly_once(self):
        payload = self._payload(
            forecast_outlook={
                "recommendation": "Recommended action: monitor the crop."
            }
        )
        raw, squeezed = self._render(payload)
        self.assertNotIn("Recommendation: Recommended action:", raw)
        self.assertEqual(
            squeezed.count("Recommendedaction:monitorthecrop."), 1
        )


_CLEAN_CAPTION = re.compile(r"Best image(?! with the detected regions)")

_FORMER_VERDICT_PREFIXES = (
    "Authoritative assess" + "ment (",
    "Comparative assess" + "ment (",
)


@skipUnless(_PDFTOTEXT, "pdftotext (poppler-utils) is not installed")
class TestForecastOutlookRows(TestCase):
    """The outlook table carries one
    row per forecast day with the weather-rule level under "Risk (weather
    rules)" and the fuzzy level under "Risk (fuzzy path)", the source and the
    fetch time; a missing forecast prints no table. Extracted with
    `pdftotext -layout` and whitespace-collapsed per line, the same assertions the edge-side copy of the renderer is held to."""

    _DAYS = [
        ("2026-09-22", 24.0, 12.0, 72.0, 0.4, 3.1, "Moderate", "Low"),
        ("2026-09-23", 18.5, 9.0, 80.0, 2.2, 4.0, "Low", "Moderate"),
        ("2026-09-24", 29.0, 15.0, 55.0, 0.0, 2.0, "High", "not evaluated"),
        ("2026-09-25", 21.0, 11.0, 74.0, 0.0, 2.5, "Low", None),
        ("2026-09-26", 15.0, 6.0, 66.0, 0.3, None, "Low", "Low"),
    ]

    def _lines(self, forecast_outlook):
        import copy
        import subprocess

        from utils.pest_session_pdf_core import render_pest_session_pdf

        payload = copy.deepcopy(_SAMPLE_PAYLOAD)
        payload["forecast_outlook"] = forecast_outlook
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "outlook_test.pdf")
            render_pest_session_pdf(payload, lambda variant: None, path)
            out = subprocess.run(
                ["pdftotext", "-layout", path, "-"], capture_output=True, text=True, check=True
            ).stdout
        return [" ".join(line.split()) for line in out.splitlines()]

    def _outlook(self):
        return {
            "source": "openweathermap",
            "fetched_at": "2026-09-21T09:30:15+00:00",
            "location": {"lat": 45.0, "lon": 10.0},
            "days": [
                {
                    "date": d, "temp_max_C": tmax, "temp_min_C": tmin, "humidity_pct": hum,
                    "precipitation_mm": rain, "wind_speed_ms": wind, "risk_level": risk,
                    "fuzzy_level": fuzzy,
                }
                for d, tmax, tmin, hum, rain, wind, risk, fuzzy in self._DAYS
            ],
            "outlook_summary": (
                "The weather rules give High risk on 1 of the 5 days (24 September), "
                "Moderate on the others."
            ),
            "recommendation": "Recommended action: monitor the crop.",
        }

    def test_five_days_render_five_rows_with_both_levels_the_source_and_the_time(self):
        lines = self._lines(self._outlook())

        self.assertIn(
            "Date Max °C Min °C Humidity % Precip mm Wind m/s Risk (weather rules) Risk (fuzzy path)",
            lines,
        )
        rows = [line for line in lines if line.startswith("2026-09-2")]
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0], "2026-09-22 24.0 12.0 72.0 0.4 3.1 Moderate Low")
        self.assertEqual(rows[1], "2026-09-23 18.5 9.0 80.0 2.2 4.0 Low Moderate")
        self.assertIn("Forecast risk outlook: the next 5 days", lines)
        self.assertIn("Source openweathermap", lines)
        self.assertIn("Data fetched at 2026-09-21T09:30:15+00:00", lines)

    def test_a_history_sourced_outlook_is_labelled_as_the_days_after_the_session(self):
        outlook = {**self._outlook(), "days_source": "history"}

        lines = self._lines(outlook)

        self.assertIn("Risk outlook: the five days after the session", lines)
        self.assertNotIn("Forecast risk outlook: the next 5 days", lines)
        self.assertIn("Location 45.0, 10.0", lines)
        self.assertIn("Source openweathermap", lines)
        self.assertIn("Data fetched at 2026-09-21T09:30:15+00:00", lines)
        self.assertFalse([line for line in lines if "Forecast Location" in line or "Forecast Source" in line])

    def test_a_fuzzy_level_the_producer_gave_as_a_string_prints_as_it_is(self):
        rows = [line for line in self._lines(self._outlook()) if line.startswith("2026-09-2")]

        self.assertEqual(rows[2], "2026-09-24 29.0 15.0 55.0 0.0 2.0 High not evaluated")

    def test_a_day_without_a_fuzzy_level_or_a_wind_value_prints_n_a(self):
        rows = [line for line in self._lines(self._outlook()) if line.startswith("2026-09-2")]

        self.assertEqual(rows[3], "2026-09-25 21.0 11.0 74.0 0.0 2.5 Low n/a")
        self.assertEqual(rows[4], "2026-09-26 15.0 6.0 66.0 0.3 n/a Low Low")

    def test_the_table_has_no_comparative_column_and_no_raw_none(self):
        lines = self._lines(self._outlook())

        table = [line for line in lines if line.startswith(("Date Max", "2026-09-2"))]
        self.assertEqual(len(table), 6)
        self.assertFalse([line for line in table if "Comparative" in line or "None" in line])

    def test_the_summary_prints_as_the_producer_composed_it(self):
        text = " ".join(self._lines(self._outlook()))

        self.assertIn(
            "Outlook Summary: The weather rules give High risk on 1 of the 5 days "
            "(24 September), Moderate on the others.",
            text,
        )

    def test_a_missing_forecast_prints_no_table_but_keeps_the_recommendation(self):
        lines = self._lines(
            {
                "unavailable_reason": "weather service unavailable",
                "recommendation": "Recommended action: monitor the crop.",
            }
        )

        self.assertFalse([line for line in lines if "Wind m/s" in line or "Risk (fuzzy path)" in line])
        self.assertIn("Recommended action: monitor the crop.", lines)

    def test_a_reason_alone_prints_no_empty_section(self):
        lines = self._lines({"unavailable_reason": "weather service unavailable"})

        self.assertNotIn("Forecast risk outlook: the next 5 days", lines)
        self.assertNotIn("Risk outlook: the five days after the session", lines)
