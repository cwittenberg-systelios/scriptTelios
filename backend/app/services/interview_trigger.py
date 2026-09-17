"""
interview_trigger.py
────────────────────
Inhaltliche Trigger im Interview-Dialog (v19.24, B3) - vorerst nur
Suizidalitaet. REGELBASIERT, KEIN LLM: ob nachgefragt wird, entscheiden
Marker; das LLM darf hier nichts entscheiden. Reine Funktionen.

Nachfrage-Kette (max. zwei Glieder, D1=A: Anrede als Platzhalter):

  Stufe 0 -> Antwort nennt Suizidalitaet oder NSSV und ist NICHT distanziert
             -> Glied 1: Plaene/Handlungen + Absprachefaehigkeit?
  Stufe 1 -> Antwort auf Glied 1 nennt Plaene, Handlungen oder fehlende
             Absprachefaehigkeit -> Glied 2: Was wurde vereinbart?
             (Kooperationsbedingung, aerztliche Information, Nachtdienst)
  Stufe 2 -> Kette beendet.

Die Kette laeuft fuer JEDE Frage des Sets, nicht nur fuer die Pflichtfrage
- Suizidalitaet kann bei "Inhalte" oder "Ergebnis" zur Sprache kommen.
Trigger-Nachfragen zaehlen NICHT auf die Grenze "eine LLM-Rueckfrage je
Frage" (C1: Trigger zuerst, Aspekt-Check entfaellt dann fuer diese Frage).

Marker (D2=A): die engen Marker aus suizidalitaet.py (v19.22) plus NSSV.
Distanzierung wird ueber eigene Marker erkannt; im Zweifel wird
nachgefragt - eine ueberfluessige Nachfrage kostet zehn Sekunden, eine
fehlende die Rechtssicherheit der Doku.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.suizidalitaet import mentions_nssv, mentions_suizidalitaet

TRIGGER_SUIZIDALITAET = "suizidalitaet"

# Formulierungen, die die Frage nach Suizidalitaet VERNEINEN. Nur wenn die
# Antwort ausschliesslich solche Muster (und keinen positiven Befund)
# enthaelt, wird nicht nachgefragt.
_DISTANZ_MARKERS: tuple[str, ...] = (
    "keine hinweise", "kein hinweis", "keine anzeichen", "kein anzeichen",
    "nicht suizidal", "keine suizid", "keine lebensmüd", "keine lebensmued",
    "distanziert", "verneint", "verneinung",
    "glaubhaft absprachefähig", "glaubhaft absprachefaehig",
    "nein,", "nein.", "nein ", "nichts dergleichen", "nichts in der richtung",
)

# Formulierungen, die einen POSITIVEN Befund tragen - sie ueberstimmen die
# Distanz-Marker ("keine Plaene, aber lebensmuede Gedanken geaeussert").
_POSITIV_MARKERS: tuple[str, ...] = (
    "geäußert", "geaeussert", "berichtet", "erwähnt", "erwaehnt",
    "gibt es", "gab es", "hat sich", "schneidet", "geschnitten",
    "ritzt", "geritzt", "versuch", "bejaht",
)

# Glied-1-Antwort: Plaene/Handlungen/fehlende Absprachefaehigkeit.
_PLAN_MARKERS: tuple[str, ...] = (
    "plan", "pläne", "plaene", "handlung", "vorbereit", "methode",
    "konkret", "versuch",
)
# Fehlende/unsichere Absprachefaehigkeit - ueberstimmt jede Verneinung.
_ABSPRACHE_NEGATIV: tuple[str, ...] = (
    "nicht absprachefähig", "nicht absprachefaehig",
    "keine absprachefähigkeit", "keine absprachefaehigkeit",
    "eingeschränkt absprachefähig", "eingeschraenkt absprachefaehig",
    "unsicher", "nicht glaubhaft", "fraglich",
)
_PLAN_VERNEINT: tuple[str, ...] = (
    "keine pläne", "keine plaene", "kein plan", "keine handlung",
    "keine konkreten", "nichts konkretes", "absprachefähig", "absprachefaehig",
)


def _has(text: str, markers: tuple[str, ...]) -> bool:
    t = (text or "").lower()
    return any(m in t for m in markers)


def _distanziert(text: str) -> bool:
    """True, wenn die Antwort Suizidalitaet verneint und keinen positiven
    Befund traegt."""
    t = (text or "").lower()
    if not _has(t, _DISTANZ_MARKERS):
        return False
    return not _has(t, _POSITIV_MARKERS)


def _plaene_genannt(text: str) -> bool:
    t = (text or "").lower()
    if _has(t, _ABSPRACHE_NEGATIV):
        return True
    if not _has(t, _PLAN_MARKERS):
        return False
    # "keine Plaene, absprachefaehig" -> verneint (ohne einschraenkendes "aber").
    if _has(t, _PLAN_VERNEINT) and not _has(t, ("aber", "jedoch", "allerdings")):
        return False
    return True


@dataclass(frozen=True)
class TriggerResult:
    trigger: str | None          # TRIGGER_SUIZIDALITAET oder None
    stufe: int                   # naechste Stufe (0 = keine Kette aktiv)
    nachfrage: str | None


def glied_1(anrede: str) -> str:
    return (f"Gab es konkrete Pläne oder Handlungen, und ist {anrede} aktuell "
            "absprachefähig?")


def glied_2(anrede: str) -> str:
    return ("Was wurde dazu vereinbart – Kooperationsbedingung, ärztliche "
            "Information, Nachtdienst?")


def pruefe_trigger(antwort: str, *, stufe: int = 0, anrede: str | None = None) -> TriggerResult:
    """Entscheidet ueber die naechste Trigger-Nachfrage.

    stufe: 0 = normale Antwort; 1 = Antwort auf Glied 1; 2 = Antwort auf
    Glied 2 (Kette beendet).
    """
    ref = anrede or "die Person"
    text = antwort or ""
    if stufe >= 2:
        return TriggerResult(None, 0, None)
    if stufe == 1:
        if _plaene_genannt(text):
            return TriggerResult(TRIGGER_SUIZIDALITAET, 2, glied_2(ref))
        return TriggerResult(None, 0, None)
    # stufe 0
    if not (mentions_suizidalitaet(text) or mentions_nssv(text)):
        return TriggerResult(None, 0, None)
    if _distanziert(text):
        return TriggerResult(None, 0, None)
    return TriggerResult(TRIGGER_SUIZIDALITAET, 1, glied_1(ref))
