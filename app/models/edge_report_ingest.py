from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from db.base_class import Base


class EdgeReportIngest(Base):
    """Index row for a report SYNCED from
    the edge node, not rendered here -- the additive counterpart to
    SessionReportIndex (which only ever describes RS-rendered reports).
    A new table rather than an extra column on SessionReportIndex: that
    table's ``payload_json``/``images_embedded`` columns are specific to
    the render-then-embed-images workflow and do not fit an
    already-rendered-PDF ingest.

    Idempotent on (session_id, content_hash): a retried upload after an interrupted sync
    returns the existing row's ``report_uuid`` rather than writing a
    duplicate row or a duplicate PDF.
    """

    __tablename__ = "edge_report_ingest"
    __table_args__ = (
        UniqueConstraint("session_id", "content_hash", name="uq_edge_report_ingest_session_hash"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, nullable=False, index=True)
    content_hash = Column(String, nullable=False)
    report_uuid = Column(String, nullable=False, unique=True)
    pdf_file_name = Column(String, nullable=False)
    user_id = Column(String, nullable=False)
    uploaded_by_user_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
