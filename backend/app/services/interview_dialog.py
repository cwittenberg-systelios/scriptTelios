"""
interview_dialog.py
───────────────────
Rueckfrage-Logik des Interview-Modus (v19.23, D1=C).

Ablauf pro Frage (Frontend fuehrt den Dialog, dieses Modul entscheidet
nur ueber die eine erlaubte Rueckfrage):

    Frage -> Antwort -> POST /api/interview/turn
        -> {"rueckfrage": null}  ->  Weiter
        -> {"rueckfrage": "…"}   ->  Antwort auf Rueckfrage -> Weiter
                                     (KEIN zweiter turn-Call: das Frontend
                                      setzt rueckfrage_bereits=True und
                                      bekommt deterministisch null)

Entscheidungsregeln, in dieser Reihenfolge:
  1. Es gab schon eine Rueckfrage zu dieser Frage       -> keine (D1: max. eine)
  2. Frage hat keine Pflichtaspekte                     -> keine (nichts zu pruefen)
  3. Antwort leer und Frage ist Pflicht                 -> deterministische
                                                          Rueckfrage, KEIN LLM
  4. Antwort leer, Frage nicht Pflicht                  -> keine ("nicht erhoben"
                                                          ist eine gueltige Antwort)
  5. sonst LLM-Check (Structured Output): welche Pflichtaspekte fehlen?
     Fehlt keiner -> keine Rueckfrage. Fehlt einer -> die vom LLM
     formulierte Rueckfrage (max. ein Satz).
  6. LLM-Fehler / unparsebares JSON -> keine Rueckfrage (der Dialog darf
     nie am Rueckfrage-Check haengen bleiben; wird geloggt).

Modell: D3=A - das Generierungsmodell des Workflows "dokumentation"
(gemma4:31b), ueber ensure_generation_model gegen die geladenen Modelle
validiert. Der Call ist klein (wenige hundert Tokens), Latenz haengt am
Kaltstart des Modells - deshalb waermt das Frontend beim Oeffnen des
Interview-Tabs nichts vor; das erste Turn laedt das Modell.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.core.interview_sets import get_frage

logger = logging.getLogger(__name__)

MAX_RUECKFRAGE_CHARS = 300

RUECKFRAGE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "fehlende_aspekte": {"type": "array", "items": {"type": "string"}},
        "rueckfrage": {"type": "string"},
    },
    "required": ["fehlende_aspekte", "rueckfrage"],
}

SYSTEM_PROMPT = (
    "Du bist ein Dokumentationsassistent in einer psychosomatischen Klinik. "
    "Ein Behandler beantwortet nach einer Therapiestunde kurze Fragen, damit "
    "daraus eine Dokumentation entsteht. Deine einzige Aufgabe: pruefen, ob "
    "die Antwort die genannten Pflichtaspekte inhaltlich abdeckt.\n\n"
    "REGELN:\n"
    "- Sei grosszuegig: ein Aspekt gilt als abgedeckt, wenn er sinngemaess, "
    "auch knapp oder umgangssprachlich, beantwortet ist. Auch 'nichts', "
    "'keine', 'nicht erhoben' oder 'weiss ich nicht' decken einen Aspekt ab.\n"
    "- Nenne unter fehlende_aspekte NUR Aspekte, zu denen die Antwort GAR "
    "NICHTS sagt.\n"
    "- Fehlt nichts: rueckfrage ist ein leerer String.\n"
    "- Fehlt etwas: formuliere GENAU EINE kurze, freundliche Rueckfrage in "
    "der Du-Form (ein Satz, maximal 25 Woerter), die nur nach dem Fehlenden fragt.\n"
    "- Bewerte nicht die fachliche Qualitaet, gib keine Ratschlaege, "
    "erfinde keine Inhalte.\n"
    "- Antworte ausschliesslich als JSON gemaess Schema."
)


@dataclass
class TurnRequest:
    set_key: str
    frage_key: str
    frage_text: str
    antwort: str
    pflicht: bool = False
    pflichtaspekte: list[str] = field(default_factory=list)
    rueckfrage_bereits: bool = False
    bisherige: list[dict] = field(default_factory=list)   # [{frage, antwort}]


@dataclass
class TurnResult:
    rueckfrage: str | None
    fehlende_aspekte: list[str]
    quelle: str          # "keine" | "deterministisch" | "llm" | "llm_fehler"
    model_used: str | None = None


def _resolve_aspekte(req: TurnRequest) -> tuple[bool, list[str]]:
    """Pflicht-Flag und Aspekte: Frontend-Werte gewinnen (editierbare
    Fragen), sonst Server-Default der Frage."""
    pflicht = req.pflicht
    aspekte = [a.strip() for a in (req.pflichtaspekte or []) if a and a.strip()]
    f = get_frage(req.set_key, req.frage_key)
    if f is not None:
        pflicht = pflicht or f.pflicht
        if not aspekte:
            aspekte = list(f.pflichtaspekte)
    return pflicht, aspekte


def deterministic_rueckfrage(frage_text: str) -> str:
    return ("Diese Frage ist eine Pflichtfrage - magst du sie kurz beantworten? "
            f"({frage_text.strip().rstrip('?')}?)")


def build_user_content(req: TurnRequest, aspekte: list[str]) -> str:
    parts = []
    if req.bisherige:
        parts.append("BISHERIGER DIALOG (nur Kontext, nicht bewerten):")
        for b in req.bisherige[-6:]:
            fq = str(b.get("frage", "")).strip()
            an = str(b.get("antwort", "")).strip() or "(nicht erhoben)"
            if fq:
                parts.append(f"- {fq}\n  -> {an}")
        parts.append("")
    parts.append(f"AKTUELLE FRAGE: {req.frage_text.strip()}")
    parts.append("PFLICHTASPEKTE:")
    parts.extend(f"- {a}" for a in aspekte)
    parts.append("")
    parts.append(f"ANTWORT DES BEHANDLERS:\n{req.antwort.strip()}")
    parts.append("")
    parts.append("Pruefe die Abdeckung und antworte als JSON.")
    return "\n".join(parts)


def _clean_rueckfrage(text: str) -> str:
    t = " ".join((text or "").split()).strip()
    if len(t) > MAX_RUECKFRAGE_CHARS:
        t = t[:MAX_RUECKFRAGE_CHARS].rsplit(" ", 1)[0].rstrip(",;:") + " …"
    return t


async def decide_turn(req: TurnRequest, *, model: str | None = None,
                      generate_fn=None) -> TurnResult:
    """Entscheidet ueber die Rueckfrage (siehe Modul-Docstring).

    generate_fn: Injektionspunkt fuer Tests (Signatur wie llm.generate_text).
    """
    if req.rueckfrage_bereits:
        return TurnResult(None, [], "keine")

    pflicht, aspekte = _resolve_aspekte(req)
    antwort = (req.antwort or "").strip()

    if not antwort:
        if pflicht:
            return TurnResult(deterministic_rueckfrage(req.frage_text), aspekte, "deterministisch")
        return TurnResult(None, [], "keine")

    if not aspekte:
        return TurnResult(None, [], "keine")

    if generate_fn is None:
        from app.services.llm import ensure_generation_model, generate_text
        generate_fn = generate_text
        if not model:
            model = await ensure_generation_model(None, "dokumentation")

    user = build_user_content(req, aspekte)
    try:
        result = await generate_fn(
            SYSTEM_PROMPT, user, max_tokens=200, model=model,
            workflow="dokumentation", temperature_override=0.1,
            response_format=RUECKFRAGE_SCHEMA,
        )
    except Exception as e:  # noqa: BLE001 - Dialog darf nie haengen bleiben
        logger.warning("interview_dialog: LLM-Check fehlgeschlagen (%s) - keine Rueckfrage", e)
        return TurnResult(None, [], "llm_fehler", model)

    data = result.get("structured_data") if isinstance(result, dict) else None
    if not isinstance(data, dict):
        logger.warning("interview_dialog: kein parsebares JSON - keine Rueckfrage")
        return TurnResult(None, [], "llm_fehler", result.get("model_used") if isinstance(result, dict) else model)

    fehlend = [str(a).strip() for a in (data.get("fehlende_aspekte") or []) if str(a).strip()]
    rueckfrage = _clean_rueckfrage(str(data.get("rueckfrage") or ""))
    model_used = result.get("model_used") or model

    if not fehlend or not rueckfrage:
        # Inkonsistente LLM-Antwort (fehlend ohne Frage oder Frage ohne
        # fehlend) wird als "nichts fehlt" gewertet - konservativ Richtung
        # weniger Rueckfragen.
        return TurnResult(None, fehlend if rueckfrage else [], "llm", model_used)
    return TurnResult(rueckfrage, fehlend, "llm", model_used)
