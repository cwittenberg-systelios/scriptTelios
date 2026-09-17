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
    ABSCHNITTE, KLIENT_KEY, SELBSTGEFAEHRDUNG_KEY, get_frage,
)

logger = logging.getLogger(__name__)

MAX_ANTWORT_CHARS = 8000
MAX_EINTRAEGE = 30


class InterviewNachfrage(BaseModel):
    """v19.24: weitere Frage-Antwort-Paare zu einem Eintrag - Trigger-Kette
    (typ="trigger:suizidalitaet") oder Aspekt-Rueckfrage (typ="aspekt")."""
    typ: str = Field(default="aspekt", max_length=64)
    frage: str = Field(min_length=1, max_length=1000)
    antwort: str = Field(default="", max_length=MAX_ANTWORT_CHARS)


class AbschlussPunkt(BaseModel):
    """v19.24 (B4): ein Punkt des Abschluss-Checks. belassen=True heisst:
    der Behandler hat bewusst nicht geantwortet - das wird so dokumentiert,
    damit das Modell nichts auffuellt."""
    typ: str = Field(default="luecke", max_length=32)     # widerspruch|luecke|plausibilitaet
    bezug: list[str] = Field(default_factory=list, max_length=12)
    frage: str = Field(min_length=1, max_length=1000)
    antwort: str = Field(default="", max_length=MAX_ANTWORT_CHARS)
    belassen: bool = False


class InterviewEintrag(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    frage: str = Field(min_length=1, max_length=1000)
    antwort: str = Field(default="", max_length=MAX_ANTWORT_CHARS)
    rueckfrage: str = Field(default="", max_length=1000)
    rueckfrage_antwort: str = Field(default="", max_length=MAX_ANTWORT_CHARS)
    # v19.24: Trigger-/Aspekt-Nachfragen als Liste (rueckfrage/rueckfrage_antwort
    # bleiben fuer v19.23-Clients erhalten und werden zusaetzlich gelesen).
    nachfragen: list[InterviewNachfrage] = Field(default_factory=list, max_length=6)
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
        return bool(self.antwort.strip() or self.rueckfrage_antwort.strip()
                    or any(n.antwort.strip() for n in self.nachfragen))

    def alle_nachfragen(self) -> list[InterviewNachfrage]:
        """Legacy-Rueckfrage (v19.23) + Nachfragen-Liste in Dialogreihenfolge."""
        out: list[InterviewNachfrage] = []
        if self.rueckfrage.strip():
            out.append(InterviewNachfrage(typ="aspekt", frage=self.rueckfrage,
                                          antwort=self.rueckfrage_antwort))
        out.extend(self.nachfragen)
        return out


class InterviewProtokoll(BaseModel):
    set: str = Field(min_length=1, max_length=64)
    set_label: str = Field(default="", max_length=120)
    eintraege: list[InterviewEintrag] = Field(min_length=1, max_length=MAX_EINTRAEGE)
    # v19.24 (B4): Ergebnis des Abschluss-Checks.
    abschluss: list[AbschlussPunkt] = Field(default_factory=list, max_length=6)
    # v19.24: Session-ID des Dialogs (Prompt-Log + Feedback-Fallkopie).
    session_id: str = Field(default="", max_length=64)

    @property
    def hat_selbstgefaehrdung(self) -> bool:
        return any(e.key == SELBSTGEFAEHRDUNG_KEY and e.beantwortet
                   for e in self.eintraege)

    def antwort_selbstgefaehrdung(self) -> str:
        for e in self.eintraege:
            if e.key == SELBSTGEFAEHRDUNG_KEY:
                teile = [e.antwort.strip()] + [n.antwort.strip() for n in e.alle_nachfragen()]
                return " ".join(t for t in teile if t)
        return ""

    def klient(self) -> dict | None:
        """v19.24 (B2): {anrede, initial} aus der Klient-Frage, oder None."""
        for e in self.eintraege:
            if e.key == KLIENT_KEY:
                return extract_klient(e.antwort) or extract_klient(e.rueckfrage_antwort)
        return None


def extract_klient(antwort: str | None) -> dict | None:
    """Anrede + Initiale aus einer Klient-Antwort ("Herr Müller", "Frau K.",
    "es geht um Herrn M"). Voller Nachname wird auf die Initiale gekuerzt
    (Kuerzel-Regel). None, wenn keine Anrede+Name erkennbar."""
    import re
    from app.services.extraction import parse_explicit_patient_name
    if not antwort or not antwort.strip():
        return None
    t = " ".join(antwort.split())
    m = re.search(r"\b(Herrn?|Frau)\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-]*\.?)", t)
    if not m:
        return None
    anrede = "Herr" if m.group(1).startswith("Herr") else "Frau"
    pn = parse_explicit_patient_name(f"{anrede} {m.group(2)}")
    if not pn or not pn.get("initial"):
        return None
    initial = pn["initial"]
    if not initial.endswith("."):
        initial = initial[0].upper() + "."
    return {"anrede": anrede, "initial": initial, "gender": "m" if anrede == "Herr" else "w"}


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
    n = 0
    for e in p.eintraege:
        if e.key == KLIENT_KEY:
            continue   # v19.24: Meta-Frage, nicht in den Prompt
        n += 1
        abschnitt = _abschnitt_label(e, p.set)
        ziel = f" [Zielabschnitt: {abschnitt}]" if abschnitt else ""
        lines.append(f"FRAGE {n}{ziel}: {e.frage.strip()}")
        a = e.antwort.strip()
        lines.append(f"ANTWORT {n}: {a if a else '(nicht erhoben)'}")
        for nf in e.alle_nachfragen():
            label = "NACHFRAGE (Suizidalität)" if nf.typ.startswith("trigger") else "RÜCKFRAGE"
            lines.append(f"{label} {n}: {nf.frage.strip()}")
            ra = nf.antwort.strip()
            lines.append(f"ANTWORT AUF {label.split(' ')[0]} {n}: {ra if ra else '(nicht erhoben)'}")
        lines.append("")
    if p.abschluss:
        lines.append("ABSCHLUSS-CHECK (Nachfragen zum gesamten Protokoll):")
        for i, ap in enumerate(p.abschluss, 1):
            lines.append(f"NACHFRAGE (Abschluss) {i}: {ap.frage.strip()}")
            if ap.belassen or not ap.antwort.strip():
                lines.append(f"ANTWORT {i}: Bewusst offen gelassen – NICHT ergänzen, nicht auffüllen.")
            else:
                lines.append(f"ANTWORT {i}: {ap.antwort.strip()}")
        lines.append("")
    return "\n".join(lines).rstrip()


def protokoll_plaintext(p: InterviewProtokoll) -> str:
    """Nur die Antworten (fuer Glossar-Konditionalitaet, Suizid-Quellcheck,
    Stil-Retrieval) - ohne Frage-Geruest, damit die Fragen selbst keine
    Marker ausloesen (z.B. das Wort 'Suizidalität' in der Pflichtfrage)."""
    parts = []
    for e in p.eintraege:
        if e.key == KLIENT_KEY:
            continue
        if e.antwort.strip():
            parts.append(e.antwort.strip())
        for nf in e.alle_nachfragen():
            if nf.antwort.strip():
                parts.append(nf.antwort.strip())
    for ap in p.abschluss:
        if ap.antwort.strip() and not ap.belassen:
            parts.append(ap.antwort.strip())
    return "\n".join(parts)
