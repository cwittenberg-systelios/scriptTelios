"""
interview_protokoll.py
──────────────────────
Protokoll des Interview-Dialogs (v19.23): Validierung und Rendering.

Das Frontend schickt das Protokoll als JSON-String im Form-Feld
`interview_protokoll` von POST /api/jobs/generate. Hier wird es geparst,
validiert (Pydantic) und in den Quellblock gerendert, den
prompts.build_user_content(interview_text=...) in den Prompt setzt.

Reine Funktionen, kein LLM, kein State - analog quality_check.py.

Format (Frontend -> Backend):
    {
      "set": "kunst",
      "set_label": "Kunsttherapie",
      "eintraege": [
        {"key": "anliegen", "frage": "...", "antwort": "...",
         "rueckfrage": "...", "rueckfrage_antwort": "..."},
        ...
      ]
    }

Die Fragen werden mitgeschickt (nicht nur die Keys), weil sie im UI
editierbar sind (E6) - das Protokoll muss die tatsaechlich gestellte
Frage enthalten, nicht den Server-Default.
"""
from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.core.interview_sets import (
    ABSCHNITTE, SELBSTGEFAEHRDUNG_KEY, get_frage,
)

logger = logging.getLogger(__name__)

MAX_ANTWORT_CHARS = 8000
MAX_EINTRAEGE = 30


class InterviewEintrag(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    frage: str = Field(min_length=1, max_length=1000)
    antwort: str = Field(default="", max_length=MAX_ANTWORT_CHARS)
    rueckfrage: str = Field(default="", max_length=1000)
    rueckfrage_antwort: str = Field(default="", max_length=MAX_ANTWORT_CHARS)
    # Optional vom Frontend mitgeschickt; fehlt es, wird der Server-Default
    # der Frage (set/key) herangezogen.
    ziel_abschnitt: str | None = None

    @field_validator("key")
    @classmethod
    def _key_slug(cls, v: str) -> str:
        v = v.strip()
        if not all(c.isalnum() or c in "_-" for c in v):
            raise ValueError("key darf nur Buchstaben, Ziffern, '_' und '-' enthalten")
        return v

    @property
    def beantwortet(self) -> bool:
        return bool(self.antwort.strip() or self.rueckfrage_antwort.strip())


class InterviewProtokoll(BaseModel):
    set: str = Field(min_length=1, max_length=64)
    set_label: str = Field(default="", max_length=120)
    eintraege: list[InterviewEintrag] = Field(min_length=1, max_length=MAX_EINTRAEGE)

    @property
    def hat_selbstgefaehrdung(self) -> bool:
        return any(e.key == SELBSTGEFAEHRDUNG_KEY and e.beantwortet
                   for e in self.eintraege)

    def antwort_selbstgefaehrdung(self) -> str:
        for e in self.eintraege:
            if e.key == SELBSTGEFAEHRDUNG_KEY:
                return " ".join(t for t in (e.antwort.strip(), e.rueckfrage_antwort.strip()) if t)
        return ""


class InterviewProtokollError(ValueError):
    """Nutzerverstaendliche Fehlermeldung (wird als 422 ausgeliefert)."""


def parse_protokoll(raw: str | None) -> InterviewProtokoll | None:
    """JSON-String -> Protokoll. None/leer -> None (kein Interview-Modus).

    Raises InterviewProtokollError bei ungueltigem Inhalt - der Job wird
    dann gar nicht erst angelegt (kein halbes Protokoll im Prompt).
    """
    if raw is None or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        raise InterviewProtokollError(f"Interview-Protokoll ist kein gültiges JSON: {e}") from e
    try:
        p = InterviewProtokoll.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", ()))
        raise InterviewProtokollError(
            f"Interview-Protokoll ungültig ({loc or 'struktur'}): {first.get('msg', e)}"
        ) from e
    if not any(e.beantwortet for e in p.eintraege):
        raise InterviewProtokollError(
            "Interview-Protokoll enthält keine einzige Antwort - die "
            "Dokumentation wurde NICHT erstellt, um ein erfundenes Dokument zu verhindern."
        )
    if not p.hat_selbstgefaehrdung:
        # E8: Pflichtfrage. Ohne Antwort darauf kein Job - sonst muesste der
        # v19.22-Standardsatz auf einer Luecke stehen.
        raise InterviewProtokollError(
            "Die Pflichtfrage zur Selbstgefährdung ist nicht beantwortet. "
            "Bitte im Interview beantworten, bevor die Dokumentation erstellt wird."
        )
    return p


def _abschnitt_label(e: InterviewEintrag, set_key: str) -> str:
    key = e.ziel_abschnitt
    if not key:
        f = get_frage(set_key, e.key)
        key = f.ziel_abschnitt if f else None
    return ABSCHNITTE.get(key or "", "")


def render_protokoll(p: InterviewProtokoll) -> str:
    """Rendert das Protokoll als Quellblock fuer den User-Content.

    Nicht beantwortete Fragen werden ausdruecklich als 'nicht erhoben'
    markiert, damit das Modell die Luecke nicht auffuellt.
    """
    lines: list[str] = []
    header = p.set_label or p.set
    lines.append(f"Verfahren / Fragen-Set: {header}")
    lines.append("")
    for i, e in enumerate(p.eintraege, 1):
        abschnitt = _abschnitt_label(e, p.set)
        ziel = f" [Zielabschnitt: {abschnitt}]" if abschnitt else ""
        lines.append(f"FRAGE {i}{ziel}: {e.frage.strip()}")
        a = e.antwort.strip()
        lines.append(f"ANTWORT {i}: {a if a else '(nicht erhoben)'}")
        if e.rueckfrage.strip():
            lines.append(f"RÜCKFRAGE {i}: {e.rueckfrage.strip()}")
            ra = e.rueckfrage_antwort.strip()
            lines.append(f"ANTWORT AUF RÜCKFRAGE {i}: {ra if ra else '(nicht erhoben)'}")
        lines.append("")
    return "\n".join(lines).rstrip()


def protokoll_plaintext(p: InterviewProtokoll) -> str:
    """Nur die Antworten (fuer Glossar-Konditionalitaet, Suizid-Quellcheck,
    Stil-Retrieval) - ohne Frage-Geruest, damit die Fragen selbst keine
    Marker ausloesen (z.B. das Wort 'Suizidalität' in der Pflichtfrage)."""
    parts = []
    for e in p.eintraege:
        if e.antwort.strip():
            parts.append(e.antwort.strip())
        if e.rueckfrage_antwort.strip():
            parts.append(e.rueckfrage_antwort.strip())
    return "\n".join(parts)
