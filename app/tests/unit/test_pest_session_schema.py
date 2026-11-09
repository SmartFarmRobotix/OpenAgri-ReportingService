import os
from pathlib import Path
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
    "SQLALCHEMY_DATABASE_URI": "sqlite:///./test.db",
    "REPORTING_SERVICE_NAME": "mock_service",
    "REPORTING_SERVICE_PORT": "8000",
    "REPORTING_GATEKEEPER_BASE_URL": "http://mock.gatekeeper",
    "JWT_ACCESS_TOKEN_EXPIRATION_TIME": "3600",
    "JWT_SIGNING_KEY": "mock_jwt_secret",
    "PDF_DIRECTORY": "/",
    "REPORTING_USING_GATEKEEPER": "True",
}


class TestForecastOutlookSurvivesRequestParsing(TestCase):
    """A regression guard: the request schema must declare `forecast_outlook`,
    otherwise pydantic drops it before the renderer sees it, independent of how
    the caller composes the field."""

    def setUp(self):
        for k, v in REQUIRED_ENV_VARS.items():
            os.environ[k] = v

    def test_forecast_outlook_present_in_request_and_model_dump(self):
        from schemas.contract import DetectorProvenance, ReportingEnvelope
        from schemas.pest_session import PestSessionReportRequest

        envelope = ReportingEnvelope(
            session_id="sess-1",
            best_image_id="img-1",
            image_count=1,
            time_start="2026-01-01T00:00:00Z",
            time_end="2026-01-01T01:00:00Z",
            provenance=DetectorProvenance(
                detector_impl_id="lookup-detector",
                detector_version="1.0.0",
                designation_status="designated",
                aggregation_rule_version="topfrac-v1",
                target_class_set=["brassica_seedling"],
                confidence_floor=0.3,
            ),
        )
        payload = PestSessionReportRequest(
            contract_version="1.2.0",
            envelope=envelope,
            infestation_score=0.8,
            forecast_outlook={"recommendation": "Immediate intervention recommended for BRVCBR."},
        )

        dumped = payload.model_dump()
        self.assertIn("forecast_outlook", dumped)
        self.assertEqual(
            dumped["forecast_outlook"]["recommendation"],
            "Immediate intervention recommended for BRVCBR.",
        )

    def test_forecast_outlook_defaults_to_none(self):
        from schemas.contract import DetectorProvenance, ReportingEnvelope
        from schemas.pest_session import PestSessionReportRequest

        envelope = ReportingEnvelope(
            session_id="sess-1",
            best_image_id="img-1",
            image_count=1,
            time_start="2026-01-01T00:00:00Z",
            time_end="2026-01-01T01:00:00Z",
            provenance=DetectorProvenance(
                detector_impl_id="lookup-detector",
                detector_version="1.0.0",
                designation_status="designated",
                aggregation_rule_version="topfrac-v1",
                target_class_set=["brassica_seedling"],
                confidence_floor=0.3,
            ),
        )
        payload = PestSessionReportRequest(envelope=envelope, infestation_score=0.1)
        self.assertIsNone(payload.model_dump()["forecast_outlook"])


def _request(**fields):
    from schemas.contract import DetectorProvenance, ReportingEnvelope
    from schemas.pest_session import PestSessionReportRequest

    envelope = ReportingEnvelope(
        session_id="sess-1",
        best_image_id="img-1",
        image_count=1,
        time_start="2026-01-01T00:00:00Z",
        time_end="2026-01-01T01:00:00Z",
        provenance=DetectorProvenance(
            detector_impl_id="lookup-detector",
            detector_version="1.0.0",
            designation_status="designated",
            aggregation_rule_version="topfrac-v1",
            target_class_set=["brassica_seedling"],
            confidence_floor=0.3,
        ),
    )
    return PestSessionReportRequest(envelope=envelope, infestation_score=0.8, **fields)


class TestRiskDetailsAndOutlookSurviveRequestParsing(TestCase):
    """The fields the report
    producer now sends must reach the renderer, not be dropped here."""

    def setUp(self):
        for k, v in REQUIRED_ENV_VARS.items():
            os.environ[k] = v

    def test_the_full_assessment_details_are_accepted_and_kept(self):
        dumped = _request(
            risk_assessment={
                "level": "Medium",
                "details": "Weather conditions: Medium risk. Detections: Medium. Overall: Medium.",
                "assessment_details_full": "Weather rules for Cabbage aphid over the session window: ...",
            }
        ).model_dump()

        self.assertEqual(
            dumped["risk_assessment"]["assessment_details_full"],
            "Weather rules for Cabbage aphid over the session window: ...",
        )
        self.assertTrue(dumped["risk_assessment"]["details"].startswith("Weather conditions:"))

    def test_a_request_without_the_full_details_still_validates(self):
        dumped = _request(risk_assessment={"level": "Medium", "details": "x"}).model_dump()

        self.assertIsNone(dumped["risk_assessment"]["assessment_details_full"])

    def test_fuzzy_details_is_accepted_and_kept(self):
        dumped = _request(
            risk_assessment={
                "level": "Medium",
                "details": "x",
                "fuzzy_details": "Fuzzy model (Mamdani) over the same window: ...",
            }
        ).model_dump()

        self.assertEqual(
            dumped["risk_assessment"]["fuzzy_details"],
            "Fuzzy model (Mamdani) over the same window: ...",
        )

    def test_a_request_without_fuzzy_details_still_validates(self):
        dumped = _request(risk_assessment={"level": "Medium", "details": "x"}).model_dump()

        self.assertIsNone(dumped["risk_assessment"]["fuzzy_details"])

    def test_a_populated_outlook_keeps_its_rows_levels_and_reason(self):
        day = {
            "date": "2026-09-22", "temp_max_C": 24.0, "temp_min_C": 12.0, "humidity_pct": 72.0,
            "precipitation_mm": 0.4, "wind_speed_ms": None, "risk_level": "Moderate",
            "fuzzy_level": "not evaluated",
        }
        populated = _request(
            forecast_outlook={
                "source": "openweathermap",
                "fetched_at": "2026-09-21T09:30:15+00:00",
                "location": {"lat": 45.0, "lon": 10.0},
                "days": [day],
                "days_source": "history",
                "outlook_summary": "s",
                "recommendation": "Recommended action: monitor the crop.",
            }
        ).model_dump()["forecast_outlook"]
        self.assertEqual(populated["days"], [day])
        self.assertEqual(populated["fetched_at"], "2026-09-21T09:30:15+00:00")
        self.assertEqual(populated["days_source"], "history")

        unavailable = _request(
            forecast_outlook={
                "unavailable_reason": "weather service unavailable",
                "recommendation": "Recommended action: monitor the crop.",
            }
        ).model_dump()["forecast_outlook"]
        self.assertEqual(unavailable["unavailable_reason"], "weather service unavailable")


class TestWeatherSummaryContradictionRefused(TestCase):
    """A summary claiming unavailability while
    carrying real weather data is a composer bug -- the schema refuses it
    at the cloud boundary (the renderer's data-wins precedence covers only
    schema-less callers such as the edge mirror)."""

    @classmethod
    def setUpClass(cls):
        for key, value in REQUIRED_ENV_VARS.items():
            os.environ.setdefault(key, value)

    def test_reason_with_source_refused(self):
        from pydantic import ValidationError
        from schemas.pest_session import WeatherSummary

        with self.assertRaises(ValidationError):
            WeatherSummary(source="open-meteo", unavailable_reason="offline")

    def test_reason_with_observed_refused(self):
        from pydantic import ValidationError
        from schemas.pest_session import WeatherSummary

        with self.assertRaises(ValidationError):
            WeatherSummary(
                observed={"temperature_2m_max_C": 4.1},
                unavailable_reason="offline",
            )

    def test_reason_alone_accepted(self):
        from schemas.pest_session import WeatherSummary

        summary = WeatherSummary(unavailable_reason="position unavailable")
        self.assertEqual(summary.unavailable_reason, "position unavailable")


class TestPolishFieldsSurviveRequestParsing(TestCase):
    """The new optional
    presentation fields must survive the request-schema boundary --
    where an undeclared field is silently dropped."""

    @classmethod
    def setUpClass(cls):
        for key, value in REQUIRED_ENV_VARS.items():
            os.environ.setdefault(key, value)

    def _envelope(self):
        from schemas.contract import DetectorProvenance, ReportingEnvelope

        return ReportingEnvelope(
            session_id="sess-polish",
            best_image_id="img-1",
            image_count=1,
            time_start="2026-02-13T07:23:26.820000+00:00",
            time_end="2026-02-13T07:57:49.040000+00:00",
            provenance=DetectorProvenance(
                detector_impl_id="lookup-detector",
                detector_version="1.0.0",
                designation_status="stub",
                aggregation_rule_version="topfrac-v1",
                target_class_set=["brassica_seedling"],
                confidence_floor=0.3,
            ),
        )

    def test_site_label_engine_provenance_and_weather_survive(self):
        from schemas.pest_session import PestSessionReportRequest

        payload = PestSessionReportRequest(
            envelope=self._envelope(),
            infestation_score=0.4,
            site_label="Field site, region",
            risk_reasoning={
                "nn_score": 0.4,
                "engine_provenance": [
                    "authoritative path: pdm-rule-engine v0.2.0, fusion v1-max-of-bands",
                ],
            },
            weather_summary={
                "source": "open-meteo (history)",
                "observed": {"temperature_2m_max_C": 4.1},
            },
        )
        dumped = payload.model_dump()
        self.assertEqual(dumped["site_label"], "Field site, region")
        self.assertEqual(
            dumped["risk_reasoning"]["engine_provenance"],
            ["authoritative path: pdm-rule-engine v0.2.0, fusion v1-max-of-bands"],
        )
        self.assertEqual(dumped["weather_summary"]["source"], "open-meteo (history)")
        self.assertEqual(
            dumped["weather_summary"]["observed"]["temperature_2m_max_C"], 4.1
        )

    def test_pest_scientific_name_survives(self):
        from schemas.pest_session import PestSessionReportRequest

        payload = PestSessionReportRequest(
            envelope=self._envelope(),
            infestation_score=0.4,
            pest_crop_context={
                "pest_name": "Cabbage aphid",
                "pest_scientific_name": "Brevicoryne brassicae",
                "pest_eppo_code": "BRVCBR",
            },
        )
        dumped = payload.model_dump()["pest_crop_context"]
        self.assertEqual(dumped["pest_scientific_name"], "Brevicoryne brassicae")

    def test_pest_scientific_name_is_optional(self):
        from schemas.pest_session import PestSessionReportRequest

        payload = PestSessionReportRequest(
            envelope=self._envelope(),
            infestation_score=0.4,
            pest_crop_context={"pest_name": "Cabbage aphid"},
        )
        self.assertIsNone(payload.model_dump()["pest_crop_context"]["pest_scientific_name"])

    def test_reason_only_weather_survives(self):
        from schemas.pest_session import PestSessionReportRequest

        payload = PestSessionReportRequest(
            envelope=self._envelope(),
            infestation_score=0.0,
            weather_summary={"unavailable_reason": "position unavailable"},
        )
        dumped = payload.model_dump()
        self.assertEqual(
            dumped["weather_summary"]["unavailable_reason"], "position unavailable"
        )


class TestEnvelopePestSurvivesRequestParsing(TestCase):
    """The request schema's envelope
    is the mirrored `ReportingEnvelope`, which gains the optional `pest`
    (an EPPO code) at contract 1.5.0. The wire form with and without it
    must parse, and the version gate must accept 1.5.0."""

    @classmethod
    def setUpClass(cls):
        for key, value in REQUIRED_ENV_VARS.items():
            os.environ.setdefault(key, value)

    @staticmethod
    def _wire(**envelope_extra):
        envelope = {
            "session_id": "sess-pest",
            "best_image_id": "img-1",
            "image_count": 1,
            "time_start": "2026-02-13T07:23:26.820000+00:00",
            "time_end": "2026-02-13T07:57:49.040000+00:00",
            "provenance": {
                "detector_impl_id": "lookup-detector",
                "detector_version": "1.0.0",
                "designation_status": "stub",
                "aggregation_rule_version": "topfrac-v1",
                "target_class_set": ["brassica_seedling"],
                "confidence_floor": 0.3,
            },
        }
        envelope.update(envelope_extra)
        return {"contract_version": "1.5.0", "envelope": envelope, "infestation_score": 0.4}

    def test_an_envelope_with_a_pest_parses_and_keeps_it(self):
        from schemas.pest_session import PestSessionReportRequest

        request = PestSessionReportRequest.model_validate(self._wire(pest="BRVCBR"))
        self.assertEqual(request.envelope.pest, "BRVCBR")
        self.assertEqual(request.model_dump(exclude_none=True)["envelope"]["pest"], "BRVCBR")

    def test_an_envelope_without_a_pest_parses(self):
        from schemas.pest_session import PestSessionReportRequest

        request = PestSessionReportRequest.model_validate(self._wire())
        self.assertIsNone(request.envelope.pest)
        self.assertNotIn("pest", request.model_dump(exclude_none=True)["envelope"])

    def test_a_malformed_pest_is_refused(self):
        from pydantic import ValidationError
        from schemas.pest_session import PestSessionReportRequest

        with self.assertRaises(ValidationError) as caught:
            PestSessionReportRequest.model_validate(self._wire(pest="brevicoryne brassicae"))
        self.assertIn("pest", str(caught.exception))

    def test_the_version_gate_accepts_1_5_0(self):
        from schemas.contract import CONTRACT_VERSION, check_contract_version

        self.assertEqual(CONTRACT_VERSION, "1.5.0")
        self.assertTrue(check_contract_version("1.5.0").accepted)

    def test_the_pest_crop_context_reason_survives_request_parsing(self):
        from schemas.pest_session import PestSessionReportRequest

        reason = "No catalogue record exists for MYZUPE on Cabbage / Brassica crops (Brassica oleracea)."
        wire = self._wire(pest="MYZUPE")
        wire["pest_crop_context_unavailable_reason"] = reason
        request = PestSessionReportRequest.model_validate(wire)
        self.assertEqual(request.model_dump()["pest_crop_context_unavailable_reason"], reason)

        self.assertIsNone(
            PestSessionReportRequest.model_validate(self._wire()).model_dump()[
                "pest_crop_context_unavailable_reason"
            ]
        )

