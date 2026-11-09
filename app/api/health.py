import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from db.session import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def liveness():
    """
    Liveness probe.

    Must return 2xx as long as the process itself is up and able to serve
    requests -- deliberately has NO dependency on the database or any
    downstream service (GateKeeper, etc.) so a degraded dependency never
    takes the whole process out of rotation.
    """
    return {"status": "ok"}


@router.get("/ready")
def readiness():
    """
    Readiness probe.

    Checks that RS's own SQL database is reachable via the existing
    SessionLocal/db.session machinery. Returns a 503 with a "degraded"
    status when the DB cannot be reached; 200 otherwise.
    """
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Readiness check failed: database unreachable: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "reason": "database unreachable"},
        )

    return {"status": "ok"}
