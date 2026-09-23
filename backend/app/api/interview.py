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
import re
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
    # v19.24
    trigger_stufe: int = Field(default=0, ge=0, le=2)
    anrede: Optional[str] = Field(default=None, max_length=32)
    frage_index: int = Field(default=0, ge=0, le=99)
    vorherige_phrase: Optional[str] = Field(default=None, max_length=120)
    session_id: Optional[str] = Field(default=None, max_length=64)


class TurnOut(BaseModel):
    rueckfrage: Optional[str]
    fehlende_aspekte: list[str]
    quelle: str
    model_used: Optional[str] = None
    # v19.24
    rueckfrage_typ: Optional[str] = None
    trigger_stufe: int = 0
    quittung: Optional[str] = None
    ueberleitung: Optional[str] = None
    klient: Optional[dict] = None


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
        trigger_stufe=req.trigger_stufe,
        anrede=req.anrede,
        frage_index=req.frage_index,
        vorherige_phrase=req.vorherige_phrase,
    )
    model = None
    if req.model and req.model.strip():
        from app.services.llm import ensure_generation_model
        model = await ensure_generation_model(req.model, "dokumentation")

    # v19.24: eine Session-ID fuer alle Calls eines Dialogs (Prompt-Log +
    # Feedback-Fallkopie finden zusammen). Fallback: je Call.
    call_id = _session_call_id(req.session_id)
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
        rueckfrage_typ=res.rueckfrage_typ,
        trigger_stufe=res.trigger_stufe,
        quittung=res.quittung,
        ueberleitung=res.ueberleitung,
        klient=res.klient,
    )


_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{4,64}$")


def _session_call_id(session_id: Optional[str]) -> str:
    if session_id and _SESSION_RE.match(session_id):
        return f"interview-{session_id}"
    return f"interview-{uuid.uuid4().hex[:8]}"


# ── v19.24 (B4): Abschluss-Check ─────────────────────────────────────────────

class AbschlussIn(BaseModel):
    protokoll: dict
    model: Optional[str] = None
    session_id: Optional[str] = Field(default=None, max_length=64)


class AbschlussPunktOut(BaseModel):
    typ: str
    bezug: list[str]
    frage: str


class AbschlussOut(BaseModel):
    punkte: list[AbschlussPunktOut]
    model_used: Optional[str] = None


@router.post("/interview/abschluss", response_model=AbschlussOut)
async def interview_abschluss(
    req: AbschlussIn,
    current_user: str = Depends(get_current_user),
) -> AbschlussOut:
    """Einmaliger Check ueber das ganze Protokoll: bis zu drei Fragen an
    den Behandler (Widerspruch, Luecke, Plausibilitaet)."""
    import json as _json
    from app.services.interview_abschluss import (
        SYSTEM_PROMPT as _ABS_SYS, build_user_content as _abs_user, pruefe_abschluss,
    )
    from app.services.interview_protokoll import InterviewProtokollError, parse_protokoll
    try:
        p = parse_protokoll(_json.dumps(req.protokoll))
    except InterviewProtokollError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if p is None:
        raise HTTPException(status_code=422, detail="Leeres Protokoll.")

    model = None
    if req.model and req.model.strip():
        from app.services.llm import ensure_generation_model
        model = await ensure_generation_model(req.model, "dokumentation")

    call_id = _session_call_id(req.session_id)
    punkte, model_used = await pruefe_abschluss(p, model=model)
    try:
        k = p.klient()
        _anrede = f"{k['anrede']} {k['initial']}" if k else None
        _log_prompt(call_id, "dokumentation", f"interview_abschluss:{p.set}",
                    _ABS_SYS, _abs_user(p, _anrede))
        _log_output(call_id, "dokumentation", f"interview_abschluss:{p.set}",
                    _json.dumps(punkte, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.debug("Interview-Abschluss: Prompt-Log fehlgeschlagen", exc_info=True)
    logger.info("Interview-Abschluss %s (user=%s set=%s): %d Punkte",
                call_id, current_user, p.set, len(punkte))
    return AbschlussOut(punkte=[AbschlussPunktOut(**x) for x in punkte], model_used=model_used)


def _aspekte_for_log(turn: TurnRequest) -> tuple[bool, list[str]]:
    from app.services.interview_dialog import _resolve_aspekte
    return _resolve_aspekte(turn)


# ── v19.31 (S3): Dialog-Modus - Streaming-Turn ───────────────────────────────
#
# POST /api/interview/chat/stream
#   Eingabe: Set, Historie (Frontend haelt sie, D2=B), Checkliste, Klient,
#            Budget-Zaehler, Trigger-Stufe, Modell, Session-ID.
#   SSE:     data: {"type":"delta","text":"…"}      gestreamter `sage`-Text
#            data: {"type":"meta", …apply_turn()…}  Checkliste, fertig, Regie
#            data: {"type":"done"}
#            data: {"type":"error","error_msg":"…"}
#
# Der Endpoint ist zustandslos: plan_turn() rekonstruiert den Zustand aus
# dem Request, apply_turn() liefert den neuen Zustand im meta-Event zurueck.

class ChatTurnIn(BaseModel):
    set: str = Field(min_length=1, max_length=64)
    historie: list[dict] = Field(default_factory=list, max_length=400)
    checkliste: dict[str, str] = Field(default_factory=dict)
    rueckfragen: dict[str, int] = Field(default_factory=dict)
    trigger_stufe: int = Field(default=0, ge=0, le=2)
    klient: Optional[dict] = None
    model: Optional[str] = None
    session_id: Optional[str] = Field(default=None, max_length=64)
    max_rueckfragen_thema: int = Field(default=4, ge=1, le=20)
    max_rueckfragen_gesamt: int = Field(default=24, ge=1, le=100)


@router.post("/interview/chat/stream")
async def interview_chat_stream(
    req: ChatTurnIn,
    current_user: str = Depends(get_current_user),
):
    """Ein Turn des Dialog-Modus, gestreamt als SSE."""
    import json as _json
    from starlette.responses import StreamingResponse
    from app.core.interview_sets import get_set
    from app.services.interview_chat import (
        TURN_SCHEMA, ChatConfig, ChatState, Turn, apply_turn, plan_turn,
    )
    from app.services.llm_chat import generate_chat_stream

    if get_set(req.set) is None:
        raise HTTPException(status_code=422, detail=f"Unbekanntes Fragen-Set: {req.set}")

    historie = [
        Turn(rolle=str(t.get("rolle") or "behandler"), text=str(t.get("text") or ""),
             thema=str(t.get("thema") or ""))
        for t in req.historie if isinstance(t, dict)
    ]
    state = ChatState(
        set_key=req.set, historie=historie, checkliste=dict(req.checkliste),
        rueckfragen_je_thema={k: int(v) for k, v in req.rueckfragen.items()},
        trigger_stufe=req.trigger_stufe,
        klient=req.klient if isinstance(req.klient, dict) and req.klient.get("initial") else None,
    )
    cfg = ChatConfig(max_rueckfragen_thema=req.max_rueckfragen_thema,
                     max_rueckfragen_gesamt=req.max_rueckfragen_gesamt)
    plan = plan_turn(state, cfg)

    model = None
    if req.model and req.model.strip():
        from app.services.llm import ensure_generation_model
        model = await ensure_generation_model(req.model, "dokumentation")
    else:
        from app.services.llm import ensure_generation_model
        model = await ensure_generation_model(None, "dokumentation")

    call_id = _session_call_id(req.session_id)
    turn_no = sum(1 for t in historie if t.rolle == "system") + 1
    try:
        _log_prompt(call_id, "dokumentation", f"interview_chat:{req.set}:t{turn_no}",
                    plan.system_prompt, _json.dumps(plan.messages, ensure_ascii=False))
    except Exception:  # noqa: BLE001
        logger.debug("Interview-Chat: Prompt-Log fehlgeschlagen", exc_info=True)

    def _sse(obj: dict) -> str:
        return f"data: {_json.dumps(obj, ensure_ascii=False)}\n\n"

    async def gen():
        result = None
        try:
            async for kind, payload in generate_chat_stream(
                plan.system_prompt, plan.messages, model=model, max_tokens=400,
                temperature=0.3, response_format=TURN_SCHEMA, stream_field="sage",
            ):
                if kind == "delta":
                    yield _sse({"type": "delta", "text": payload})
                elif kind == "error":
                    yield _sse({"type": "error", "error_msg": str(payload)})
                    return
                elif kind == "done":
                    result = payload
        except Exception as e:  # noqa: BLE001
            logger.warning("Interview-Chat %s: Stream-Fehler %s", call_id, e)
            yield _sse({"type": "error", "error_msg": str(e)})
            return
        if result is None:
            yield _sse({"type": "error", "error_msg": "Keine Antwort vom Modell."})
            return
        data = result.get("structured_data")
        sage = (result.get("sage") or "").strip()
        if not sage and isinstance(data, dict):
            sage = str(data.get("sage") or "").strip()
        if not sage:
            sage = (result.get("text") or "").strip()[:600]
        meta = apply_turn(state, data if isinstance(data, dict) else None, sage, plan, cfg)
        meta.update({"type": "meta", "sage": sage, "regie": plan.regie,
                     "model_used": result.get("model_used"), "duration_s": result.get("duration_s"),
                     "parse_error": bool(result.get("structured_parse_error"))})
        try:
            _log_output(call_id, "dokumentation", f"interview_chat:{req.set}:t{turn_no}",
                        result.get("text") or "", {"duration_s": result.get("duration_s"),
                                                   "token_count": result.get("token_count"),
                                                   "regie_typ": plan.regie_typ})
        except Exception:  # noqa: BLE001
            logger.debug("Interview-Chat: Output-Log fehlgeschlagen", exc_info=True)
        logger.info("Interview-Chat %s t%d (user=%s set=%s): regie=%s fertig=%s",
                    call_id, turn_no, current_user, req.set, plan.regie_typ, meta.get("fertig"))
        yield _sse(meta)
        yield _sse({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
