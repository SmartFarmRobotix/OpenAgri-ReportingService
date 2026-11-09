from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Optional, Tuple

import requests
from fastapi import HTTPException

from core import settings
from db.session import SessionLocal
from models import SessionReportIndex
from utils.pest_session_images import local_image_bytes
from utils.pest_session_pdf_core import PdfRenderError, render_pest_session_pdf

logger = logging.getLogger(__name__)


def _fetch_image_bytes(best_image_id: str, token: str, variant: str) -> Optional[Tuple[bytes, str]]:
    """
    Fetch image bytes from InferenceService via GateKeeper proxy.

    Parameters
    ----------
    best_image_id: str
        The image_id hash stored in the session metadata.
    token: str
        GateKeeper JWT bearer token.
    variant: str
        ``"clean"`` (the capture without annotations) or ``"annotated"`` (the
        same capture with the detected regions drawn). Never ``"bytes"``: the
        content-addressed raw route can answer with an image another session
        uploaded, so no report is built from it.
    """
    if not best_image_id:
        return None

    if not settings.REPORTING_USING_GATEKEEPER:
        return None

    base = str(settings.REPORTING_GATEKEEPER_BASE_URL).strip("/")
    service_name = settings.REPORTING_INFERENCE_SERVICE_NAME
    url = f"{base}/api/proxy/{service_name}/image/{best_image_id}/{variant}"

    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        if str(response.status_code)[0] != "2":
            logger.warning(
                "Inference image fetch (%s) failed: %s", variant, response.status_code
            )
            return None
        if not response.content:
            logger.warning("Inference image fetch (%s) returned empty content", variant)
            return None
        content_type = response.headers.get("Content-Type", "image/jpeg").split(";")[0]
        return response.content, content_type
    except requests.RequestException as exc:
        logger.warning("Inference image fetch (%s) exception: %s", variant, exc)
        return None


def _make_image_source(session_id: str, best_image_id: str, token: str):
    """The injected image-source interface that
    `render_pest_session_pdf` (pest_session_pdf_core.py) actually calls.
    This closure owns the cloud/mixed fetch STRATEGY (fallback chaining),
    the core rendering module owns none of it.

    Per variant, in order:
      1. the local push-store, keyed by session_id;
      2. the GK-proxied pull of that same variant from the session's own
         variant route.

    There is no third step: the content-addressed raw-bytes route is not
    consulted, because it serves an image that any other session uploaded
    with the same content (a report could otherwise carry another
    session's image).
    """

    def _image_source(variant: str) -> Optional[Tuple[bytes, str]]:
        bundle = local_image_bytes(session_id, variant) if session_id else None
        if bundle:
            return bundle
        return _fetch_image_bytes(best_image_id, token, variant=variant)

    return _image_source


def upsert_session_report_index(
    db,
    session_id: str,
    pdf_file_name: str,
    payload: Dict[str, Any],
    images_embedded: bool,
) -> None:
    """
    Create or update the session's `SessionReportIndex` row and commit.

    user_id/report_uuid are recovered from pdf_file_name's own
    "{user_id}/{uuid}" shape rather than threading two more parameters
    through every call site.
    """
    user_id, _, report_uuid = pdf_file_name.partition("/")
    row = db.query(SessionReportIndex).filter(
        SessionReportIndex.session_id == session_id
    ).first()
    if row is None:
        row = SessionReportIndex(session_id=session_id)
        db.add(row)
    row.user_id = user_id
    row.report_uuid = report_uuid
    row.pdf_file_name = pdf_file_name
    row.payload_json = json.dumps(payload)
    row.images_embedded = images_embedded
    db.commit()


_RENDER_LOCKS = tuple(threading.Lock() for _ in range(16))


def _render_lock(session_id: str) -> threading.Lock:
    return _RENDER_LOCKS[hash(session_id) % len(_RENDER_LOCKS)]


def create_pest_session_report_pdf(
    payload: Dict[str, Any],
    token: str,
    pdf_file_name: str,
) -> bool:
    """
    Generate a pest session PDF report from the orchestration payload.
    Returns whether BOTH image variants were embedded in it.

    Thin cloud wrapper around the
    extracted, settings-decoupled `render_pest_session_pdf`
    (pest_session_pdf_core.py): this function owns exactly the RS-specific
    couplings -- resolving image bytes via GateKeeper or
    the local push-store, and indexing the render into `SessionReportIndex` --
    and nothing about PDF layout itself, which now lives in the shared
    component so the edge-side copy of this renderer renders through the
    identical code.
    """
    envelope = payload.get("envelope") or {}
    best_image_id = envelope.get("best_image_id", "")
    session_id = envelope.get("session_id", "")

    image_source = _make_image_source(session_id, best_image_id, token)

    pdf_dir = f"{settings.PDF_DIRECTORY}{pdf_file_name}"
    with _render_lock(session_id):
        try:
            result = render_pest_session_pdf(payload, image_source, f"{pdf_dir}.pdf")
        except PdfRenderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        images_embedded = result.images_embedded

        if session_id:
            db = SessionLocal()
            try:
                upsert_session_report_index(
                    db, session_id, pdf_file_name, payload, images_embedded
                )
            finally:
                db.close()
    return images_embedded
