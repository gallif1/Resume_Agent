"""Minimal FastAPI server for the standalone apply automation.

Runs independently from ai-job-agent. Intended for later integration.
"""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from job_apply.browser import DATA_DIR
from job_apply.engine import apply_to_job
from job_apply.models import Applicant, ApplyRequest

UPLOADS_DIR = DATA_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(
    title="Job Apply Automation",
    description="Standalone service: job URL + CV + contact details → fill & submit",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "job-apply-automation"}


@app.post("/apply")
async def apply_endpoint(
    job_url: str = Form(...),
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    cv: UploadFile = File(...),
    dry_run: bool = Form(False),
    headless: bool = Form(True),
) -> JSONResponse:
    if not job_url.strip():
        raise HTTPException(status_code=400, detail="job_url is required")

    suffix = Path(cv.filename or "resume.pdf").suffix or ".pdf"
    dest = UPLOADS_DIR / f"{uuid.uuid4().hex}{suffix}"
    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(cv.file, out)
    finally:
        await cv.close()

    request = ApplyRequest(
        job_url=job_url.strip(),
        cv_path=dest,
        applicant=Applicant(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
        ),
        dry_run=dry_run,
        headless=headless,
    )
    # Playwright sync API cannot run inside the FastAPI event loop.
    result = await asyncio.to_thread(apply_to_job, request)
    status_code = 200 if result.success else 422
    return JSONResponse(status_code=status_code, content=result.to_dict())


def main() -> None:
    import uvicorn

    uvicorn.run("job_apply.api:app", host="0.0.0.0", port=8010, reload=False)


if __name__ == "__main__":
    main()
