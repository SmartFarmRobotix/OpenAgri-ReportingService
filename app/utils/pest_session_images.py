"""Local store for the size-capped best-image
pair pushed by the edge Inference Service (via GK), keyed by session_id.

This is the additive push-side counterpart to `pest_session_report.py`'s
existing GK-proxy pull (`_fetch_image_bytes`) -- report rendering tries this
store first and falls back to the pull path only when nothing was pushed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, Tuple

from core import settings


def _session_image_dir(session_id: str) -> Path:
    return Path(settings.IMAGES_DIRECTORY) / session_id


def _hash_sidecar_path(session_id: str) -> Path:
    return _session_image_dir(session_id) / "hash.json"


def _ext_for(content_type: str) -> str:
    if content_type.endswith("png"):
        return ".png"
    if content_type.endswith("webp"):
        return ".webp"
    return ".jpg"


def local_image_bytes(session_id: str, variant: str) -> Optional[Tuple[bytes, str]]:
    """Read a previously-pushed clean/annotated image for session_id, if any."""
    sidecar = _hash_sidecar_path(session_id)
    if not sidecar.exists():
        return None
    try:
        meta = json.loads(sidecar.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    entry = meta.get(variant)
    if not entry:
        return None
    path = _session_image_dir(session_id) / entry["filename"]
    if not path.exists():
        return None
    return path.read_bytes(), entry["content_type"]


def store_best_image_pair(
    session_id: str,
    image_id: str,
    artifact_hash: str,
    clean_content_type: str,
    clean_bytes: bytes,
    annotated_content_type: str,
    annotated_bytes: bytes,
) -> bool:
    """
    Store the pushed pair, idempotent on (session_id, artifact_hash); the
    best-image pair is the only image data crossing the boundary. Returns True if newly stored/changed, False if
    this exact pair was already stored (a retried push after an
    interruption must leave exactly one copy per variant, not duplicates).
    """
    sidecar = _hash_sidecar_path(session_id)
    if sidecar.exists():
        try:
            existing = json.loads(sidecar.read_text())
        except (OSError, json.JSONDecodeError):
            existing = {}
        if existing.get("artifact_hash") == artifact_hash:
            return False

    directory = _session_image_dir(session_id)
    os.makedirs(directory, exist_ok=True)

    clean_filename = f"clean{_ext_for(clean_content_type)}"
    annotated_filename = f"annotated{_ext_for(annotated_content_type)}"
    (directory / clean_filename).write_bytes(clean_bytes)
    (directory / annotated_filename).write_bytes(annotated_bytes)

    sidecar.write_text(
        json.dumps(
            {
                "artifact_hash": artifact_hash,
                "image_id": image_id,
                "clean": {"filename": clean_filename, "content_type": clean_content_type},
                "annotated": {"filename": annotated_filename, "content_type": annotated_content_type},
            }
        )
    )
    return True
