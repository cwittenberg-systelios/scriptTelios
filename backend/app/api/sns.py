"""
POST /api/sns/docx  - DOCX der SNS-Verlaufsauswertung aus dem (ggf. im
                      Frontend editierten) Job-Ergebnis rendern (v19.41, F2=B).

Wie /ism/xml zustandslos: das Frontend schickt das Ergebnis-JSON des Jobs
(fakten, grafiken, phasen) plus den editierten Text zurueck und bekommt die
Datei. Der DOCX-Renderer lebt genau einmal (services/sns_docx.build_docx).
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.services.sns_docx import build_docx
from app.services.sns_qc import sns_issues_for_result
from app.services.quality_check import serialize_issues

router = APIRouter()
logger = logging.getLogger(__name__)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class SnsDocxRequest(BaseModel):
    kuerzel: str = Field(min_length=1, max_length=64)
    anrede: str = Field(default="Klientin", max_length=32)
    text: str = Field(min_length=1)
    result: dict[str, Any]        # Job-Ergebnis-JSON (fakten, grafiken, phasen, ...)


class SnsCheckRequest(BaseModel):
    text: str
    result: dict[str, Any]


@router.post("/sns/check", tags=["SNS"])
async def sns_check(req: SnsCheckRequest) -> dict:
    """QC auf dem editierten Text (deterministisch, Regelkatalog sns_qc)."""
    res = {**req.result, "text": req.text}
    return serialize_issues(sns_issues_for_result(res), workflow="sns_verlauf")


@router.post("/sns/docx", tags=["SNS"])
async def sns_docx(req: SnsDocxRequest) -> Response:
    res = {**req.result, "text": req.text}
    try:
        data = build_docx(res, req.kuerzel.strip(), req.anrede.strip() or "Klientin")
    except Exception as e:  # noqa: BLE001
        logger.exception("SNS-DOCX-Rendering fehlgeschlagen: %s", e)
        raise HTTPException(status_code=500, detail=f"DOCX-Rendering fehlgeschlagen: {e}") from e
    stem = re.sub(r"[^A-Za-z0-9_-]+", "", req.kuerzel.strip()) or "Klient"
    filename = f"ISM-Auswertung_{stem}.docx"
    return Response(
        content=data, media_type=DOCX_MIME,
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"},
    )
