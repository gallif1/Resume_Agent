"""FastAPI routes for the CV Tailor MVP."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

import auth
from cv_tailor.job_persist import maybe_persist_tailored_cv_to_job
from cv_tailor.parser import CvParseError
from cv_tailor.models import RegenerateCvRequest
from cv_tailor.service import (
    CvTailorError,
    generate_tailored_cv,
    get_download_pdf,
    get_stored_pdf_bytes,
    get_stored_session_snapshot,
    regenerate_tailored_cv,
)

logger = logging.getLogger("cv_tailor.routes")

router = APIRouter(prefix="/api/cv-tailor", tags=["cv-tailor"])


@router.post("/generate")
async def cv_tailor_generate(
    file: UploadFile = File(...),
    job_description: str = Form(...),
    cv_id: str | None = Form(default=None),
    job_id: int | None = Form(default=None),
    user: dict = Depends(auth.get_current_user),
):
    filename = file.filename or "cv.pdf"
    try:
        file_bytes = await file.read()
        # Playwright sync API must not run on the asyncio event loop.
        result = await asyncio.to_thread(
            generate_tailored_cv,
            file_bytes=file_bytes,
            filename=filename,
            job_description=job_description,
            user_id=str(user["id"]),
        )
    except CvParseError as exc:
        logger.warning("CV tailor parse error: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CvTailorError as exc:
        logger.warning("CV tailor generation error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    saved_to_job = maybe_persist_tailored_cv_to_job(
        cv_id=cv_id,
        job_id=job_id,
        preview_text=result.preview_text,
        user_id=str(user["id"]),
        pdf_bytes=get_stored_pdf_bytes(result_id=result.result_id, user_id=str(user["id"])),
        tailored_cv=result.tailored_cv.model_dump(),
        job_analysis=result.job_analysis.model_dump(),
        user_confirmed_facts=[fact.model_dump() for fact in result.user_confirmed_facts],
        cv_text=(get_stored_session_snapshot(result_id=result.result_id, user_id=str(user["id"])) or {}).get(
            "cv_text"
        ),
        model=result.model,
    )

    return {
        "result_id": result.result_id,
        "model": result.model,
        "preview_text": result.preview_text,
        "tailored_cv": result.tailored_cv.model_dump(),
        "job_analysis": result.job_analysis.model_dump(),
        "user_confirmed_facts": [fact.model_dump() for fact in result.user_confirmed_facts],
        "saved_to_job": saved_to_job is not None,
        "job_version_id": (saved_to_job or {}).get("version_id"),
    }


@router.post("/regenerate/{result_id}")
async def cv_tailor_regenerate(
    result_id: str,
    body: RegenerateCvRequest,
    user: dict = Depends(auth.get_current_user),
):
    try:
        result = await asyncio.to_thread(
            regenerate_tailored_cv,
            result_id=result_id,
            user_id=str(user["id"]),
            request=body,
        )
    except CvTailorError as exc:
        logger.warning("CV tailor regenerate error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    snapshot = get_stored_session_snapshot(result_id=result.result_id, user_id=str(user["id"])) or {}
    saved_to_job = maybe_persist_tailored_cv_to_job(
        cv_id=body.cv_id,
        job_id=body.job_id,
        preview_text=result.preview_text,
        user_id=str(user["id"]),
        pdf_bytes=get_stored_pdf_bytes(result_id=result.result_id, user_id=str(user["id"])),
        tailored_cv=result.tailored_cv.model_dump(),
        job_analysis=result.job_analysis.model_dump(),
        user_confirmed_facts=[fact.model_dump() for fact in result.user_confirmed_facts],
        cv_text=snapshot.get("cv_text"),
        model=result.model,
    )

    return {
        "result_id": result.result_id,
        "model": result.model,
        "preview_text": result.preview_text,
        "tailored_cv": result.tailored_cv.model_dump(),
        "job_analysis": result.job_analysis.model_dump(),
        "user_confirmed_facts": [fact.model_dump() for fact in result.user_confirmed_facts],
        "saved_to_job": saved_to_job is not None,
        "job_version_id": (saved_to_job or {}).get("version_id"),
    }


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