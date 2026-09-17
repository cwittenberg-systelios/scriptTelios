"""
interview_abschluss.py
──────────────────────
Abschluss-Check des Interview-Dialogs (v19.24, B4/C3).

Nach der letzten Frage schaut das Modell EINMAL ueber das ganze Protokoll
und nennt bis zu drei Punkte - ausschliesslich als Fragen an den
Behandler. Typen:

  widerspruch     zwei Antworten passen nicht zusammen
  luecke          eine Antwort verweist auf etwas, das nirgends steht
  plausibilitaet  eine Aussage ist fachlich ungewoehnlich oder unklar

Leitplanken (im Prompt): keine Bewertung, keine Vorschlaege, was zu
dokumentieren waere, keine Fachurteile - der Behandler bewertet.
"(nicht erhoben)" ist NUR dann eine Luecke, wenn eine andere Antwort darauf
verweist; uebersprungene Fragen sind sonst eine bewusste Entscheidung.

Jeder Punkt wird im Frontend beantwortet oder "so gelassen"; beides landet
im Protokoll (AbschlussPunkt), damit die Doku nichts auffuellt (C4).

LLM-Fehler -> leere Liste; das Interview gilt als abgeschlossen.
"""
from __future__ import annotations

import logging

from app.services.interview_protokoll import (
    InterviewProtokoll, render_protokoll,
)

logger = logging.getLogger(__name__)

MAX_PUNKTE = 3
TYPEN = ("widerspruch", "luecke", "plausibilitaet")

ABSCHLUSS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "punkte": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "typ": {"type": "string", "enum": list(TYPEN)},
                    "bezug": {"type": "array", "items": {"type": "string"}},
                    "frage": {"type": "string"},
                },
                "required": ["typ", "bezug", "frage"],
            },
        },
    },
    "required": ["punkte"],
}

SYSTEM_PROMPT = (
    "Du bist ein Dokumentationsassistent in einer psychosomatischen Klinik. "
    "Ein Behandler hat nach einer Therapiestunde Fragen beantwortet; daraus "
    "wird eine Dokumentation erstellt. Du siehst das vollstaendige Protokoll "
    "und pruefst es EINMAL auf drei Dinge:\n"
    "1. widerspruch: zwei Antworten passen nicht zusammen "
    "(z.B. 'nichts vereinbart' und spaeter 'Uebung bis naechste Woche').\n"
    "2. luecke: eine Antwort verweist auf etwas, das nirgends steht "
    "(z.B. 'wie besprochen' ohne dass es besprochen wurde). "
    "'(nicht erhoben)' allein ist KEINE Luecke - uebersprungene Fragen sind "
    "eine bewusste Entscheidung des Behandlers.\n"
    "3. plausibilitaet: eine Aussage ist fachlich ungewoehnlich oder unklar "
    "(z.B. 'Ergebnis sehr gut' bei 'geht in kritischem Zustand').\n\n"
    "REGELN:\n"
    f"- Hoechstens {MAX_PUNKTE} Punkte, die wichtigsten zuerst. Ist nichts "
    "auffaellig, ist die Liste leer - das ist der Normalfall.\n"
    "- Jeder Punkt ist GENAU EINE kurze Frage an den Behandler in der Du-Form "
    "(ein Satz, maximal 30 Woerter). Keine Bewertung, kein Ratschlag, kein "
    "Vorschlag, was zu dokumentieren waere. Der Behandler entscheidet.\n"
    "- 'bezug' nennt die Nummern der betroffenen Fragen (z.B. ['3', '5']).\n"
    "- Ist eine Anrede angegeben (z.B. 'Herr M.'), verwende sie.\n"
    "- Erfinde keine Inhalte. Antworte ausschliesslich als JSON gemaess Schema."
)


def build_user_content(p: InterviewProtokoll, anrede: str | None) -> str:
    parts = []
    if anrede:
        parts.append(f"PERSON: {anrede}")
    parts.append("PROTOKOLL:")
    parts.append(render_protokoll(p))
    parts.append("")
    parts.append("Pruefe das Protokoll und antworte als JSON.")
    return "\n".join(parts)


def _clean(text: str, limit: int = 400) -> str:
    t = " ".join((text or "").split()).strip()
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(",;:") + " …"
    return t


async def pruefe_abschluss(p: InterviewProtokoll, *, model: str | None = None,
                           generate_fn=None) -> tuple[list[dict], str | None]:
    """Liefert (punkte, model_used). punkte = [{typ, bezug, frage}], max. MAX_PUNKTE."""
    k = p.klient()
    anrede = f"{k['anrede']} {k['initial']}" if k else None

    if generate_fn is None:
        from app.services.llm import ensure_generation_model, generate_text
        generate_fn = generate_text
        if not model:
            model = await ensure_generation_model(None, "dokumentation")

    user = build_user_content(p, anrede)
    try:
        result = await generate_fn(
            SYSTEM_PROMPT, user, max_tokens=400, model=model,
            workflow="dokumentation", temperature_override=0.1,
            response_format=ABSCHLUSS_SCHEMA,
        )
    except Exception as e:  # noqa: BLE001 - Abschluss darf nie blockieren
        logger.warning("interview_abschluss: LLM-Call fehlgeschlagen (%s) - keine Punkte", e)
        return [], model

    data = result.get("structured_data") if isinstance(result, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("punkte"), list):
        logger.warning("interview_abschluss: kein parsebares JSON - keine Punkte")
        return [], (result.get("model_used") if isinstance(result, dict) else model)

    punkte: list[dict] = []
    for raw in data["punkte"]:
        if not isinstance(raw, dict):
            continue
        frage = _clean(str(raw.get("frage") or ""))
        if not frage:
            continue
        typ = str(raw.get("typ") or "luecke").strip().lower()
        if typ not in TYPEN:
            typ = "luecke"
        bezug = [str(b).strip() for b in (raw.get("bezug") or []) if str(b).strip()][:12]
        punkte.append({"typ": typ, "bezug": bezug, "frage": frage})
        if len(punkte) >= MAX_PUNKTE:
            break
    return punkte, (result.get("model_used") or model)
