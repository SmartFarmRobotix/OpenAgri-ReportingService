from sqlalchemy import Boolean, Column, DateTime, String, Text
from sqlalchemy.sql import func

from db.base_class import Base


class SessionReportIndex(Base):
    """Session id to generated-report lookup,
    so a best-image pair that lands *after* the PDF was already rendered
    (deferred delivery with bounded retries) can find and re-render it.

    ``payload_json`` caches the exact orchestration payload the PDF was
    (or will be) built from, so a re-render never needs to reconstruct it.
    ``images_embedded=False`` is this session's ``images_pending`` state -- there is no separate boolean for it, the two
    are the same fact viewed from either side.
    """

    __tablename__ = "session_report_index"

    session_id = Column(String, primary_key=True)
    user_id = Column(String, nullable=False)
    report_uuid = Column(String, nullable=False, index=True)
    pdf_file_name = Column(String, nullable=False)
    payload_json = Column(Text, nullable=False)
    images_embedded = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
