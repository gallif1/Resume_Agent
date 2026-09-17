"""FastAPI routes for the Live Job Scanner."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import auth
from live_scanner.service import get_scanner_service

logger = logging.getLogger("live_scanner.routes")

router = APIRouter(prefix="/api/live-scanner", tags=["live-scanner"])


class SourceCreate(BaseModel):
    company_name: str
    provider: str
    board_identifier: str = ""
    careers_url: str | None = None
    enabled: bool = True
    scan_interval_seconds: int | None = Field(default=None, ge=60)
    notes: str | None = None


class SourceUpdate(BaseModel):
    company_name: str | None = None
    board_identifier: str | None = None
    careers_url: str | None = None
    enabled: bool | None = None
    scan_interval_seconds: int | None = Field(default=None, ge=60)
    notes: str | None = None


@router.get("/status")
def live_scanner_status(user: dict = Depends(auth.get_current_user)) -> dict[str, Any]:
    svc = get_scanner_service(str(user["id"]))
    return svc.snapshot()


@router.post("/play")
def live_scanner_play(user: dict = Depends(auth.get_current_user)) -> dict[str, Any]:
    svc = get_scanner_service(str(user["id"]))
    return svc.play()


@router.post("/pause")
def live_scanner_pause(user: dict = Depends(auth.get_current_user)) -> dict[str, Any]:
    svc = get_scanner_service(str(user["id"]))
    return svc.pause()


@router.post("/stop")
def live_scanner_stop(user: dict = Depends(auth.get_current_user)) -> dict[str, Any]:
    svc = get_scanner_service(str(user["id"]))
    return svc.stop()


@router.post("/clear")
def live_scanner_clear(user: dict = Depends(auth.get_current_user)) -> dict[str, Any]:
    """Clear session UI counters/logs only. Does not delete baseline or jobs."""
    svc = get_scanner_service(str(user["id"]))
    return svc.clear()


@router.get("/sources")
def live_scanner_list_sources(user: dict = Depends(auth.get_current_user)) -> dict[str, Any]:
    svc = get_scanner_service(str(user["id"]))
    snap = svc.snapshot()
    return {"sources": snap["sources"], "providers": snap["providers"]}


@router.post("/sources")
def live_scanner_add_source(
    body: SourceCreate, user: dict = Depends(auth.get_current_user)
) -> dict[str, Any]:
    svc = get_scanner_service(str(user["id"]))
    try:
        source = svc.add_source(body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"source": source}


@router.patch("/sources/{source_id}")
def live_scanner_update_source(
    source_id: str,
    body: SourceUpdate,
    user: dict = Depends(auth.get_current_user),
) -> dict[str, Any]:
    svc = get_scanner_service(str(user["id"]))
    try:
        source = svc.update_source(
            source_id, body.model_dump(exclude_unset=True)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"source": source}


@router.delete("/sources/{source_id}")
def live_scanner_delete_source(
    source_id: str, user: dict = Depends(auth.get_current_user)
) -> dict[str, Any]:
    svc = get_scanner_service(str(user["id"]))
    try:
        svc.delete_source(source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}
