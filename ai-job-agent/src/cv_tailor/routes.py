"""FastAPI routes for the CV Tailor MVP."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

import auth
from cv_tailor.parser import CvParseError
from cv_tailor.models import RegenerateCvRequest
from cv_tailor.service import (
    CvTailorError,
    get_download_pdf,
    get_job_status,
    start_generate_job,
    start_regenerate_job,
)

logger = logging.getLogger("cv_tailor.routes")

router = APIRouter(prefix="/api/cv-tailor", tags=["cv-tailor"])


def _http_from_unexpected(exc: BaseException) -> HTTPException:
    """Never leak Starlette's plain-text 500 — the UI treats that as a generic Hebrew failure."""
    logger.exception("Unexpected CV tailor failure")
    detail = str(exc).strip() or exc.__class__.__name__
    return HTTPException(
        status_code=500,
        detail=f"יצירת קורות חיים מותאמים נכשלה: {detail}",
    )


@router.post("/generate")
async def cv_tailor_generate(
    file: UploadFile = File(...),
    job_description: str = Form(...),
    cv_id: str | None = Form(default=None),
    job_id: int | None = Form(default=None),
    user: dict = Depends(auth.get_current_user),
):
    """Start async generate; poll GET /api/cv-tailor/jobs/{job_id} for the result.

    Mobile browsers often kill long POSTs (~60–90s) and surface the SPA HTML as a
    fake HTTP 200. Returning immediately + short polls avoids that failure mode.
    """
    filename = file.filename or "cv.pdf"
    try:
        file_bytes = await file.read()
        job_id_async = start_generate_job(
            file_bytes=file_bytes,
            filename=filename,
            job_description=job_description,
            user_id=str(user["id"]),
            cv_id=cv_id,
            link_job_id=job_id,
        )
        return {"job_id": job_id_async, "status": "pending"}
    except CvParseError as exc:
        logger.warning("CV tailor parse error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CvTailorError as exc:
        logger.warning("CV tailor generation error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _http_from_unexpected(exc) from exc


@router.post("/regenerate/{result_id}")
async def cv_tailor_regenerate(
    result_id: str,
    body: RegenerateCvRequest,
    user: dict = Depends(auth.get_current_user),
):
    """Start async regenerate; poll GET /api/cv-tailor/jobs/{job_id} for the result."""
    try:
        job_id_async = start_regenerate_job(
            result_id=result_id,
            user_id=str(user["id"]),
            request=body,
        )
        return {"job_id": job_id_async, "status": "pending"}
    except CvTailorError as exc:
        logger.warning("CV tailor regenerate error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _http_from_unexpected(exc) from exc


@router.get("/jobs/{job_id}")
async def cv_tailor_job_status(
    job_id: str,
    user: dict = Depends(auth.get_current_user),
):
    try:
        return get_job_status(job_id=job_id, user_id=str(user["id"]))
    except CvTailorError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise _http_from_unexpected(exc) from exc


@router.get("/download/{result_id}")
async def cv_tailor_download(
    result_id: str,
    user: dict = Depends(auth.get_current_user),
):
    try:
        pdf_bytes, filename = get_download_pdf(result_id=result_id, user_id=str(user["id"]))
    except CvTailorError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
