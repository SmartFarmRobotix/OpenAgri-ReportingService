"""The report-facing request body carries the mirrored `ReportingEnvelope`
(schemas.contract); best_image_id and the detector provenance come from the envelope.
best_image_id and detector provenance come from the envelope; PDM's own
weather/risk-index enrichment rides alongside it, unchanged.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from .contract import ReportingEnvelope


class RiskAssessment(BaseModel):
    level: str
    details: Optional[str] = None
    assessment_details_full: Optional[str] = None
    fuzzy_details: Optional[str] = None


class WeatherSummary(BaseModel):
    source: Optional[str] = None
    source_note: Optional[str] = None
    location: Optional[Dict[str, Any]] = None
    variables: List[str] = Field(default_factory=list)
    observed: Optional[Dict[str, Any]] = None
    unavailable_reason: Optional[str] = None

    @model_validator(mode="after")
    def _reason_excludes_data(self) -> "WeatherSummary":
        if self.unavailable_reason and (self.source or self.observed):
            raise ValueError(
                "unavailable_reason cannot be combined with source/observed weather data"
            )
        return self


class PestCropContext(BaseModel):
    """OCSM-enriched pest and crop context forwarded from PDM."""
    pest_name: Optional[str] = None
    pest_scientific_name: Optional[str] = None
    pest_eppo_code: Optional[str] = None
    pest_eppo_url: Optional[str] = None
    pest_description: Optional[str] = None
    symptoms: Optional[str] = None
    crop_name: Optional[str] = None
    crop_scientific: Optional[str] = None
    crop_growth_stage: Optional[str] = None


class RiskReasoning(BaseModel):
    """NN score + PDMS rule evaluation + narrative produced by PDM."""
    nn_score: Optional[float] = None
    nn_verdict: Optional[str] = None
    nn_note: Optional[str] = None
    pdms_rules_evaluated: Optional[List[Dict[str, Any]]] = None
    weather_window: Optional[str] = None
    combined_level: Optional[str] = None
    narrative: Optional[str] = None
    engine_provenance: Optional[List[str]] = None


class PestSessionReportRequest(BaseModel):
    contract_version: Optional[str] = None
    envelope: ReportingEnvelope
    infestation_score: float = Field(..., ge=0.0, le=1.0)

    risk_assessment: Optional[RiskAssessment] = None
    risk_index: Optional[Dict[str, Any]] = None
    gdd: Optional[Dict[str, Any]] = None
    weather_summary: Optional[WeatherSummary] = None

    pest_crop_context: Optional[PestCropContext] = None
    pest_crop_context_unavailable_reason: Optional[str] = None
    risk_reasoning: Optional[RiskReasoning] = None
    forecast_outlook: Optional[Dict[str, Any]] = None

    site_label: Optional[str] = None


class PestSessionReportResponse(BaseModel):
    uuid: str


class BestImagePairUpload(BaseModel):
    """
    The size-capped clean+annotated
    best-image pair, pushed by the edge Inference Service via GK (JSON body
    rather than multipart -- the inference service has no multipart-encoder dependency, and
    the pair is small enough post-compression that base64 overhead is
    immaterial). ``artifact_hash`` is the idempotency key alongside the
    path's ``session_id``.
    """

    contract_version: Optional[str] = None
    image_id: str
    artifact_hash: str
    clean_content_type: str
    clean_b64: str
    annotated_content_type: str
    annotated_b64: str


class BestImagePairUploadResponse(BaseModel):
    stored: bool
    deduplicated: bool
    report_updated: bool = False
    images_embedded: bool


class SessionReportStatusResponse(BaseModel):
    session_id: str
    report_uuid: str
    images_pending: bool
    origin: str = "cloud"


class EdgeReportSyncResponse(BaseModel):
    """Response for the additive
    edge-report ingest endpoint. ``deduplicated=True`` means this exact
    (session_id, content_hash) pair was already stored -- the returned
    ``uuid`` is the EXISTING report, no new row or file was written."""

    uuid: str
    deduplicated: bool = False
