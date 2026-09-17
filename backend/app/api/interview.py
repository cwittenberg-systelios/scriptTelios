"""
Interview-Modus (v19.23) - Endpoints fuer den Dialog nach der Sitzung.

  GET  /api/interview/sets        Fragen-Sets (Manifest, Server-Defaults)
  POST /api/interview/transcribe  Kurzdiktat einer Antwort -> Text
                                  (Whisper ohne Diarisierung, synchron)
  POST /api/interview/turn        Rueckfrage-Check zu einer Antwort (D1=C)

Das Protokoll des Dialogs schickt das Frontend am Ende als JSON im
Form-Feld `interview_protokoll` von POST /api/jobs/generate - dort wird
es validiert (services/interview_protokoll) und in den Prompt gesetzt.
Eine laufende Interview-Session wird NICHT serverseitig gehalten (D4=A:
Frontend-State + Draft-Cache).

Datenschutz: es spricht ausschliesslich der Behandler; Klientendaten
kommen nur als Kuerzel vor. Die Diktat-Datei wird nach der Transkription
sofort geloescht, nichts davon landet in der DB oder im Prompt-Log
(nur die Turn-Prompts laufen ueber das reguläre Prompt-Log).
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.core.auth import get_current_user
from app.core.config import settings
from app.core.files import ALLOWED_AUDIO, upload_dir
from app.core.interview_sets import to_manifest
from app.services.interview_dialog import TurnRequest, decide_turn
from app.services.prompt_log import _log_output, _log_prompt

router = APIRouter()
logger = logging.getLogger(__name__)

# Diktate sind klein (Opus, ~180 KB/min); 20 MB ist grosszuegig und
# schuetzt trotzdem vor versehentlichen Sitzungs-Uploads.
DICTATION_MAX_MB = 20


@router.get("/interview/sets")
async def interview_sets(current_user: str = Depends(get_current_user)) -> dict:
    """Server-Defaults der Fragen-Sets. Editierbar ist alles im Frontend
    (E6); dieses Manifest ist der Reset-Punkt."""
    return to_manifest()


@router.post("/interview/transcribe")
async def interview_transcribe(
    audio: UploadFile = File(...),
    current_user: str = Depends(get_current_user),
) -> dict:
    """Transkribiert ein Kurzdiktat (eine Interview-Antwort) synchron."""
    suffix = Path(audio.filename or "diktat.webm").suffix.lower() or ".webm"
    if suffix not in ALLOWED_AUDIO:
        raise HTTPException(
            status_code=422,
            detail=f"Dateiformat '{suffix}' nicht unterstützt. "
                   f"Erlaubt: {', '.join(sorted(ALLOWED_AUDIO))}",
        )
    content = await audio.read()
    if not content:
        raise HTTPException(status_code=422, detail="Leere Audiodatei.")
    size_mb = len(content) / (1024 * 1024)
    if size_mb > DICTATION_MAX_MB:
        raise HTTPException(
            status_code=413,
            detail=f"Diktat zu groß ({size_mb:.1f} MB). Maximum: {DICTATION_MAX_MB} MB",
        )

    tmp = upload_dir() / f"diktat_{uuid.uuid4().hex}{suffix}"
    tmp.write_bytes(content)
    try:
        from app.services.transcription import transcribe_dictation
        result = await transcribe_dictation(tmp)
    except ValueError as e:
        # Diktat zu lang (DICTATION_MAX_SECONDS)
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        logger.error("Interview-Diktat: Transkription fehlgeschlagen: %s", e)
        raise HTTPException(status_code=500, detail=f"Transkription fehlgeschlagen: {e}") from e
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            logger.warning("Interview-Diktat: Tempdatei %s konnte nicht gelöscht werden", tmp.name)

    return {
        "transcript": result.get("transcript", ""),
        "duration_seconds": result.get("duration_seconds"),
        "word_count": result.get("word_count", 0),
    }


class TurnIn(BaseModel):
    set: str = Field(min_length=1, max_length=64)
    frage_key: str = Field(min_length=1, max_length=64)
    frage_text: str = Field(min_length=1, max_length=1000)
    antwort: str = Field(default="", max_length=8000)
    pflicht: bool = False
    pflichtaspekte: list[str] = Field(default_factory=list, max_length=8)
    rueckfrage_bereits: bool = False
    bisherige: list[dict] = Field(default_factory=list, max_length=30)
    model: Optional[str] = None


class TurnOut(BaseModel):
    rueckfrage: Optional[str]
    fehlende_aspekte: list[str]
    quelle: str
    model_used: Optional[str] = None


@router.post("/interview/turn", response_model=TurnOut)
async def interview_turn(
    req: TurnIn,
    current_user: str = Depends(get_current_user),
) -> TurnOut:
    """Entscheidet, ob zu dieser Antwort EINE Rueckfrage gestellt wird."""
    turn = TurnRequest(
        set_key=req.set, frage_key=req.frage_key, frage_text=req.frage_text,
        antwort=req.antwort, pflicht=req.pflicht,
        pflichtaspekte=list(req.pflichtaspekte),
        rueckfrage_bereits=req.rueckfrage_bereits,
        bisherige=[b for b in req.bisherige if isinstance(b, dict)],
    )
    model = None
    if req.model and req.model.strip():
        from app.services.llm import ensure_generation_model
        model = await ensure_generation_model(req.model, "dokumentation")

    call_id = f"interview-{uuid.uuid4().hex[:8]}"
    res = await decide_turn(turn, model=model)
    if res.quelle in ("llm", "llm_fehler"):
        # Prompt-Log nur fuer echte LLM-Calls, gleiche Datei wie alle anderen
        # Calls; call_label traegt Set und Frage.
        from app.services.interview_dialog import SYSTEM_PROMPT, build_user_content
        _, aspekte = _aspekte_for_log(turn)
        try:
            _log_prompt(call_id, "dokumentation", f"interview_turn:{req.set}:{req.frage_key}",
                        SYSTEM_PROMPT, build_user_content(turn, aspekte))
            _log_output(call_id, "dokumentation", f"interview_turn:{req.set}:{req.frage_key}",
                        f"rueckfrage={res.rueckfrage!r} fehlend={res.fehlende_aspekte} quelle={res.quelle}")
        except Exception:  # noqa: BLE001 - Logging darf den Dialog nie stoeren
            logger.debug("Interview-Turn: Prompt-Log fehlgeschlagen", exc_info=True)

    if settings.AUTH_ENABLED:
        logger.info("Interview-Turn %s (user=%s set=%s frage=%s): quelle=%s rueckfrage=%s",
                    call_id, current_user, req.set, req.frage_key, res.quelle,
                    bool(res.rueckfrage))
    return TurnOut(
        rueckfrage=res.rueckfrage,
        fehlende_aspekte=res.fehlende_aspekte,
        quelle=res.quelle,
        model_used=res.model_used,
    )


def _aspekte_for_log(turn: TurnRequest) -> tuple[bool, list[str]]:
    from app.services.interview_dialog import _resolve_aspekte
    return _resolve_aspekte(turn)
