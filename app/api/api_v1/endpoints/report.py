import base64
import binascii
import datetime
import json
import os
import uuid
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    BackgroundTasks,
)
from pydantic import UUID4
from sqlalchemy.orm import Session

from api import deps
from core import settings
from models import EdgeReportIngest, SessionReportIndex
from schemas import (
    PDF,
    BestImagePairUpload,
    BestImagePairUploadResponse,
    EdgeReportSyncResponse,
    PestSessionReportRequest,
    PestSessionReportResponse,
    SessionReportStatusResponse,
)
from schemas.contract import check_contract_version
from utils.animals_report import process_animal_data
from utils.farm_calendar_report import process_farm_calendar_data
from utils.irrig_fert_pest_report import process_irrigation_fertilization_data
from utils.pest_session_report import (
    create_pest_session_report_pdf,
    upsert_session_report_index,
)
from utils.pest_session_images import store_best_image_pair
from fastapi.responses import FileResponse

router = APIRouter()


@router.get("/{report_id}/", response_class=FileResponse)
def retrieve_generated_pdf(
    report_id: UUID4,
    token=Depends(deps.get_current_user),
):
    """

    Retrieve generated PDF file

    """
    user_id = token.id

    file_path = f"{settings.PDF_DIRECTORY}{user_id}/{report_id}.pdf"

    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=202,
            detail=f"PDF uuid {report_id} is being generated. Please be patient and try again in couple of seconds.",
        )

    return FileResponse(
        path=file_path, media_type="application/pdf", filename=f"{report_id}"
    )


@router.post("/pest-session-report/", response_model=PestSessionReportResponse)
def generate_pest_session_report(
    background_tasks: BackgroundTasks,
    payload: PestSessionReportRequest,
    db: Session = Depends(deps.get_db),
    token=Depends(deps.get_current_user),
):
    """
    Generates Pest Session Report PDF file from orchestration payload

    Accepts the versioned envelope; warns (proceeds) on a
    missing contract_version, rejects an unsupported major version.

    The session's index row (report identity, cached payload,
    images_embedded=False) is written HERE, before the render is
    scheduled: a best-image pair whose upload is processed while the
    render is still running then finds the row and re-renders, instead of
    finding nothing.
    """
    if not settings.REPORTING_USING_GATEKEEPER:
        raise HTTPException(
            status_code=400,
            detail="Pest session reports require GateKeeper mode.",
        )

    version_check = check_contract_version(payload.contract_version)
    if not version_check.accepted:
        raise HTTPException(status_code=400, detail=version_check.warning)

    uuid_v4 = str(uuid.uuid4())
    user_id = token.id
    uuid_of_pdf = f"{user_id}/{uuid_v4}"
    payload_dict = payload.model_dump()

    session_id = payload.envelope.session_id
    if session_id:
        upsert_session_report_index(
            db, session_id, uuid_of_pdf, payload_dict, images_embedded=False
        )

    background_tasks.add_task(
        create_pest_session_report_pdf,
        payload=payload_dict,
        token=token,
        pdf_file_name=uuid_of_pdf,
    )

    return PestSessionReportResponse(uuid=uuid_v4)


@router.post("/pest-session-images/{session_id}", response_model=BestImagePairUploadResponse)
def upload_pest_session_images(
    session_id: str,
    payload: BestImagePairUpload,
    db: Session = Depends(deps.get_db),
    token=Depends(deps.get_current_user),
):
    """
    Additive endpoint. The edge Inference
    Service pushes the size-capped clean+annotated best-image pair here (via
    GateKeeper's proxy) at session close -- this is the mixed-mode
    replacement for the cloud-pull path, which a NATed edge node cannot
    serve inbound requests for.

    Idempotent on (session_id, artifact_hash): a retried push after an
    interrupted delivery is a no-op here, not a duplicate write.
    """
    try:
        clean_bytes = base64.b64decode(payload.clean_b64, validate=True)
        annotated_bytes = base64.b64decode(payload.annotated_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid base64 image data: {exc}")

    newly_stored = store_best_image_pair(
        session_id=session_id,
        image_id=payload.image_id,
        artifact_hash=payload.artifact_hash,
        clean_content_type=payload.clean_content_type,
        clean_bytes=clean_bytes,
        annotated_content_type=payload.annotated_content_type,
        annotated_bytes=annotated_bytes,
    )

    report_updated = False
    images_embedded = False
    index_row = db.query(SessionReportIndex).filter(
        SessionReportIndex.session_id == session_id
    ).first()
    if index_row is not None:
        images_embedded = bool(index_row.images_embedded)
        if not images_embedded:
            cached_payload = json.loads(index_row.payload_json)
            images_embedded = create_pest_session_report_pdf(
                payload=cached_payload,
                token=token,
                pdf_file_name=index_row.pdf_file_name,
            )
            report_updated = True

    return BestImagePairUploadResponse(
        stored=True,
        deduplicated=not newly_stored,
        report_updated=report_updated,
        images_embedded=images_embedded,
    )


@router.get(
    "/pest-session-report/by-session/{session_id}/status",
    response_model=SessionReportStatusResponse,
)
def get_pest_session_report_status(
    session_id: str,
    db: Session = Depends(deps.get_db),
    token=Depends(deps.get_current_user),
):
    """
    Exposes the images_pending state so a caller can observe "report exists without the pair" and
    "pair landed, report updated" without parsing the PDF itself.
    """
    row = db.query(SessionReportIndex).filter(
        SessionReportIndex.session_id == session_id
    ).first()
    if row is not None:
        return SessionReportStatusResponse(
            session_id=session_id,
            report_uuid=row.report_uuid,
            images_pending=not row.images_embedded,
            origin="cloud",
        )
    edge_row = (
        db.query(EdgeReportIngest)
        .filter(EdgeReportIngest.session_id == session_id)
        .order_by(EdgeReportIngest.created_at.desc(), EdgeReportIngest.id.desc())
        .first()
    )
    if edge_row is None:
        raise HTTPException(status_code=404, detail="No report found for this session")
    return SessionReportStatusResponse(
        session_id=session_id,
        report_uuid=edge_row.report_uuid,
        images_pending=False,
        origin="edge",
    )


@router.post("/pest-session-report/edge-sync/", response_model=EdgeReportSyncResponse)
async def ingest_edge_synced_report(
    session_id: str = Form(...),
    content_hash: str = Form(...),
    pdf: UploadFile = File(...),
    db: Session = Depends(deps.get_db),
    token=Depends(deps.get_current_user),
):
    """
    Additive endpoint for a report
    already rendered on the edge node with the same PDF component this
    service's own `pest_session_report.py` renders through, and uploaded
    here by the edge sync worker once the platform regains connectivity. Stores the PDF
    exactly like a cloud-rendered report -- same `PDF_DIRECTORY`, same
    `{user_id}/{uuid}.pdf` layout -- so `GET /{report_id}/` retrieves an
    edge-synced report unchanged.

    Idempotent on (session_id, content_hash): a retried upload after an
    interrupted first attempt returns the EXISTING report, writing no
    new row and no new file.
    """
    existing = (
        db.query(EdgeReportIngest)
        .filter(
            EdgeReportIngest.session_id == session_id,
            EdgeReportIngest.content_hash == content_hash,
        )
        .first()
    )
    if existing is not None:
        return EdgeReportSyncResponse(uuid=existing.report_uuid, deduplicated=True)

    pdf_bytes = await pdf.read()
    uploader_id = token.id
    owner_user_id = settings.REPORTING_EDGE_REPORT_OWNER_USER_ID or uploader_id
    uuid_v4 = str(uuid.uuid4())
    pdf_file_name = f"{owner_user_id}/{uuid_v4}"
    file_path = f"{settings.PDF_DIRECTORY}{pdf_file_name}.pdf"
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "wb") as fh:
        fh.write(pdf_bytes)

    db.add(
        EdgeReportIngest(
            session_id=session_id,
            content_hash=content_hash,
            report_uuid=uuid_v4,
            pdf_file_name=pdf_file_name,
            user_id=owner_user_id,
            uploaded_by_user_id=uploader_id,
        )
    )
    db.commit()

    return EdgeReportSyncResponse(uuid=uuid_v4, deduplicated=False)


@router.post("/irrigation-report/", response_model=PDF)
async def generate_irrigation_report(
    background_tasks: BackgroundTasks,
    token=Depends(deps.get_current_user),
    irrigation_id: str = None,
    data: UploadFile = None,
    from_date: datetime.date = None,
    to_date: datetime.date = None,
    parcel_id: str = None
):
    """
    Generates Irrigation Report PDF file

    """
    uuid_v4 = str(uuid.uuid4())
    user_id = token.id
    uuid_of_pdf = f"{user_id}/{uuid_v4}"

    if not data and not settings.REPORTING_USING_GATEKEEPER:
        raise HTTPException(
            status_code=400,
            detail=f"Data file must be provided if gatekeeper is not used.",
        )
    if data:
        data = data.file.read()
    background_tasks.add_task(
        process_irrigation_fertilization_data,
        data=data,
        token=token,
        pdf_file_name=uuid_of_pdf,
        from_date=from_date,
        to_date=to_date,
        operation_id=irrigation_id,
        parcel_id=parcel_id
    )

    return PDF(uuid=uuid_v4)


@router.post("/compost-report/", response_model=PDF)
async def generate_generic_observation_report(
    background_tasks: BackgroundTasks,
    calendar_activity_type: str = None,
    token=Depends(deps.get_current_user),
    data: UploadFile = None,
    operation_id: str = None,
    from_date: datetime.date = None,
    to_date: datetime.date = None,
    parcel_id: str = None
):
    """
    Generates Observation Report PDF file
    All Farm Calendar Observation Type values are possible as input

    """

    uuid_v4 = str(uuid.uuid4())
    user_id = token.id
    uuid_of_pdf = f"{user_id}/{uuid_v4}"
    if data:
        data = data.file.read()
    background_tasks.add_task(
        process_farm_calendar_data,
        calendar_activity_type=calendar_activity_type,
        token=token,
        data=data,
        pdf_file_name=uuid_of_pdf,
        operation_id=operation_id,
        from_date=from_date,
        to_date=to_date,
        parcel_id=parcel_id
    )

    return PDF(uuid=uuid_v4)


@router.post("/animal-report/", response_model=PDF)
async def generate_animal_report(
    background_tasks: BackgroundTasks,
    token=Depends(deps.get_current_user),
    farm_animal_id: str = None,
    animal_group: Optional[str] = None,
    name: Optional[str] = None,
    parcel: Optional[UUID4] = None,
    status: Optional[int] = None,
    data: UploadFile = None,
    from_date: datetime.date = None,
    to_date: datetime.date = None,
    parcel_id: str = None
):
    """
    Generates Animal Report PDF file
    """
    uuid_v4 = str(uuid.uuid4())
    user_id = token.id
    uuid_of_pdf = f"{user_id}/{uuid_v4}"
    params = None
    if not data:
        if not settings.REPORTING_USING_GATEKEEPER:
            raise HTTPException(
                status_code=400,
                detail=f"Data file must be provided if gatekeeper is not used.",
            )

        params = {}
        if animal_group:
            params["animal_group"] = animal_group
        if name:
            params["name"] = name
        if parcel:
            params["parcel"] = str(parcel)
        if status is not None:
            params["status"] = status
        if parcel_id:
            params['parcel'] = parcel_id
    if data:
        data = data.file.read()
    background_tasks.add_task(
        process_animal_data,
        data=data,
        token=token,
        params=params,
        pdf_file_name=uuid_of_pdf,
        from_date=from_date,
        to_date=to_date,
        farm_animal_id=farm_animal_id,
    )

    return PDF(uuid=uuid_v4)


@router.post("/fertilization-report/", response_model=PDF)
async def generate_fertilization_report(
    background_tasks: BackgroundTasks,
    token=Depends(deps.get_current_user),
    fertilization_id: str = None,
    data: UploadFile = None,
    from_date: datetime.date = None,
    to_date: datetime.date = None,
    parcel_id: str = None
):
    """
    Generates Fertilization Report PDF file

    """
    uuid_v4 = str(uuid.uuid4())
    user_id = token.id
    uuid_of_pdf = f"{user_id}/{uuid_v4}"

    if not data and not settings.REPORTING_USING_GATEKEEPER:
        raise HTTPException(
            status_code=400,
            detail=f"Data file must be provided if gatekeeper is not used.",
        )
    if data:
        data = data.file.read()
    background_tasks.add_task(
        process_irrigation_fertilization_data,
        data=data,
        token=token,
        pdf_file_name=uuid_of_pdf,
        from_date=from_date,
        to_date=to_date,
        operation_id=fertilization_id,
        parcel_id=parcel_id,
        irrigation_flag=False,
        fertilization_flag=True
    )

    return PDF(uuid=uuid_v4)


@router.post("/pesticides-report/", response_model=PDF)
async def generate_pesticides_report(
    background_tasks: BackgroundTasks,
    token=Depends(deps.get_current_user),
    pesticide_id: str = None,
    data: UploadFile = None,
    from_date: datetime.date = None,
    to_date: datetime.date = None,
    parcel_id: str = None
):
    """
    Generates Pesticides Report PDF file

    """
    uuid_v4 = str(uuid.uuid4())
    user_id = token.id
    uuid_of_pdf = f"{user_id}/{uuid_v4}"

    if not data and not settings.REPORTING_USING_GATEKEEPER:
        raise HTTPException(
            status_code=400,
            detail=f"Data file must be provided if gatekeeper is not used.",
        )

    if data:
        data = data.file.read()

    background_tasks.add_task(
        process_irrigation_fertilization_data,
        data=data,
        token=token,
        pdf_file_name=uuid_of_pdf,
        from_date=from_date,
        to_date=to_date,
        operation_id=pesticide_id,
        parcel_id=parcel_id,
        irrigation_flag=False,
        pesticides_flag= True
    )

    return PDF(uuid=uuid_v4)
