"""FastAPI routes for the CV Tailor MVP."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

import auth
from cv_tailor.parser import CvParseError
from cv_tailor.service import CvTailorError, generate_tailored_cv, get_download_docx

logger = logging.getLogger("cv_tailor.routes")

router = APIRouter(prefix="/api/cv-tailor", tags=["cv-tailor"])


@router.post("/generate")
async def cv_tailor_generate(
    file: UploadFile = File(...),
    job_description: str = Form(...),
    user: dict = Depends(auth.get_current_user),
):
    filename = file.filename or "cv.pdf"
    try:
        file_bytes = await file.read()
        result = generate_tailored_cv(
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

    return {
        "result_id": result.result_id,
        "model": result.model,
        "preview_text": result.preview_text,
        "tailored_cv": result.tailored_cv.model_dump(),
    }


@router.get("/download/{result_id}")
async def cv_tailor_download(
    result_id: str,
    user: dict = Depends(auth.get_current_user),
):
    try:
        docx_bytes, filename = get_download_docx(result_id=result_id, user_id=str(user["id"]))
    except CvTailorError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )