"""
suizidalitaet.py
────────────────
Pflicht-Hinweis zur Suizidalitaet in der Gespraechsdokumentation (v19.22).

Fachliche Regel (Featurerequest c.wittenberg, 2026-09-11):
Jede Gespraechsdokumentation (Workflow "dokumentation") enthaelt am Ende
einen Hinweis zur Suizidalitaet. Entweder der Text gibt bereits Inhalte
aus dem Gespraech dazu wieder - oder es wird der Standardsatz ergaenzt:

    "Frau M. ist glaubhaft absprachefaehig, keine Anzeichen von akuter
     Suizidalitaet."

Design-Prinzipien (analog quality_check.py):
  - REGELBASIERT, KEIN LLM. Deterministisch, idempotent, testbar.
  - Reine Funktionen ohne State und ohne Seiteneffekte ausser logging.
  - Das Modul entscheidet NUR; das Setzen von QC-Issues passiert in
    quality_check.py, der Aufruf in generation_pipeline._finalize (S2).

Getroffene Entscheidungen (Sprintplan v19.22, vom Nutzer bestaetigt):
  D1=A  Ergaenzung laeuft still - kein QC-Hinweis im Normalfall.
  D2=B  Sprechen die QUELLEN ueber Suizidalitaet, der Output aber nicht,
        wird NICHT ergaenzt (der Standardsatz waere dann inhaltlich
        falsch). Stattdessen meldet der QC das als critical.
  D3=A  Freier Schlusssatz als letzter Absatz, KEINE eigene Ueberschrift.
  D4=C  Ohne belastbares Namenskuerzel wird nicht ergaenzt (das Kuerzel
        ist ein Pflichtfeld - sein Fehlen ist ein Datenproblem, kein
        Formulierungsproblem). QC meldet das als warning.
  D5=A  Selbstverletzung/NSSV ist klinisch etwas anderes als Suizidalitaet
        und erfuellt die Anforderung NICHT (siehe _NSSV_MARKERS).
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# ── Ergebnis-Status (wandert als Job-Attribut in den QualityCheck) ─────────────

STATUS_PRESENT = "present"                  # Text enthaelt bereits Gespraechsinhalte
STATUS_APPENDED = "appended"                # Standardsatz wurde ergaenzt (D1=A: still)
STATUS_SOURCE_CONFLICT = "source_conflict"  # Quelle ja, Output nein -> QC critical (D2=B)
STATUS_NO_NAME = "no_name"                  # kein belastbares Kuerzel -> QC warning (D4=C)

VALID_STATUSES = frozenset({
    STATUS_PRESENT, STATUS_APPENDED, STATUS_SOURCE_CONFLICT, STATUS_NO_NAME,
})


# ── Marker ────────────────────────────────────────────────────────────────────
#
# Bewusst ENG gehalten. Ein Falsch-Positiv hier ist teurer als ein
# Falsch-Negativ: es wuerde bedeuten, dass wir den Pflichtsatz fuer
# erledigt halten und die Doku ohne Hinweis ausliefern. Im Zweifel also
# lieber ergaenzen.
#
# Deshalb NICHT aufgenommen:
#   "sterben"     - matcht "das Sterben der Mutter", "Sterbebegleitung"
#   "tod"         - matcht Todesfall/Todestag im Trauerkontext
#   "krisenplan"  - wird regelhaft auch fuer nicht-suizidale Krisen
#                   (Dissoziation, Panik, Reizueberflutung) vereinbart
#
# Alle Eintraege sind lowercase Substrings; Umlaut- UND ae/oe/ue-Variante
# jeweils explizit, weil Outputs beides schreiben (vgl. quality_specs).
_SUIZID_MARKERS: tuple[str, ...] = (
    # Wortstamm deckt Suizid, suizidal, Suizidalitaet, Suizidgedanken,
    # Suizidversuch, parasuizidal, Antisuizidvertrag ab.
    "suizid",
    "lebensmüd", "lebensmued",
    "lebensüberdr", "lebensueberdr",
    "selbsttötung", "selbsttoetung",
    "selbstmord",
    "freitod",
    "todeswunsch", "todessehnsucht",
    # Absprachefaehigkeit wird klinisch praktisch nur im Kontext von
    # Selbst-/Fremdgefaehrdung dokumentiert - und steht im Standardsatz
    # selbst, was die Idempotenz mit traegt.
    "absprachefähig", "absprachefaehig",
    "schutzvertrag",
    # Umschreibungen
    "nicht mehr leben",
    "das leben nehmen",
    "das leben zu nehmen",
    "aus dem leben scheiden",
    "aus dem leben zu scheiden",
    "sich etwas antun",
    "sich etwas anzutun",
)

# D5=A: NSSV erfuellt die Anforderung NICHT. Die Liste steht hier nur zur
# Dokumentation der Abgrenzung und fuer Tests - sie wird bei der Erkennung
# bewusst NICHT verwendet.
_NSSV_MARKERS: tuple[str, ...] = (
    "selbstverletz", "ritzen", "geritzt", "nssv", "svv",
)


# ── Standardsatz ──────────────────────────────────────────────────────────────
#
# Wortlaut vom Nutzer vorgegeben; {ref} ist die anonymisierte Referenzform
# ("Frau M." / "Herr M."), identisch zu llm.substitute_patient_placeholders.
FALLBACK_TEMPLATE = (
    "{ref} ist glaubhaft absprachefähig, keine Anzeichen von akuter Suizidalität."
)


def mentions_suizidalitaet(text: str | None) -> bool:
    """True, wenn der Text Suizidalitaet thematisiert (Marker-basiert).

    NSSV-Begriffe zaehlen NICHT (D5=A).
    """
    if not text:
        return False
    lo = text.lower()
    return any(m in lo for m in _SUIZID_MARKERS)


def mentions_nssv(text: str | None) -> bool:
    """True bei Selbstverletzungs-Markern. Nur fuer Diagnostik/Tests -
    beeinflusst die Ergaenzungslogik bewusst nicht (D5=A)."""
    if not text:
        return False
    lo = text.lower()
    return any(m in lo for m in _NSSV_MARKERS)


def patient_reference(patient_name: dict | None) -> str | None:
    """Anonymisierte Referenzform ("Frau M.") oder None, wenn kein
    belastbares Kuerzel vorliegt.

    Plausibilitaets-Check gespiegelt von llm.substitute_patient_placeholders
    (v16-A1): lieber gar nichts einsetzen als Muell wie
    "die Klientin/der Klient ist glaubhaft absprachefaehig ...".
    """
    if not patient_name:
        return None
    initial = (patient_name.get("initial") or "").strip()
    if not initial:
        return None
    # Kuerzel auf genau einen Schlusspunkt normalisieren ("M" -> "M.").
    stem = initial.rstrip(".").strip()
    if len(stem) != 1 or not stem.isalpha():
        return None
    initial = f"{stem.upper()}."

    anrede = (patient_name.get("anrede") or "").strip()
    if anrede not in ("Frau", "Herr"):
        anrede = ""
    ref = f"{anrede} {initial}".strip()

    ref_low = ref.lower()
    if len(ref) > 12 or "klient" in ref_low or "patient" in ref_low:
        logger.warning(
            "suizidalitaet: Referenzform %r unplausibel - keine Ergaenzung", ref,
        )
        return None
    return ref


def build_suizid_fallback(patient_name: dict | None) -> str | None:
    """Standardsatz mit anonymisierter Anrede, oder None ohne Kuerzel (D4=C)."""
    ref = patient_reference(patient_name)
    if not ref:
        return None
    return FALLBACK_TEMPLATE.format(ref=ref)


def resolve_suizid_note(
    text: str,
    *,
    source_text: str = "",
    patient_name: dict | None = None,
) -> tuple[str, str]:
    """Stellt den Pflicht-Hinweis sicher. Idempotent.

    Returns:
        (text, status) - status ist eine der STATUS_*-Konstanten.
        Der Text wird NUR im Fall STATUS_APPENDED veraendert.

    Reihenfolge der Pruefung:
      1. Output thematisiert Suizidalitaet       -> STATUS_PRESENT (nichts tun)
      2. Quelle thematisiert sie, Output nicht   -> STATUS_SOURCE_CONFLICT (D2=B)
      3. Kein belastbares Namenskuerzel          -> STATUS_NO_NAME (D4=C)
      4. sonst Standardsatz als eigener Absatz   -> STATUS_APPENDED
    """
    if mentions_suizidalitaet(text):
        return text, STATUS_PRESENT

    if mentions_suizidalitaet(source_text):
        logger.warning(
            "suizidalitaet: Quelle thematisiert Suizidalitaet, Output nicht - "
            "kein Standardsatz ergaenzt (QC meldet SUIZIDALITAET_QUELLE_NICHT_UEBERNOMMEN)",
        )
        return text, STATUS_SOURCE_CONFLICT

    satz = build_suizid_fallback(patient_name)
    if not satz:
        logger.warning(
            "suizidalitaet: kein belastbares Namenskuerzel - Hinweis nicht ergaenzt",
        )
        return text, STATUS_NO_NAME

    base = (text or "").rstrip()
    if not base:
        # Leerer Output ist anderswo schon ein critical-Issue; hier nichts
        # anhaengen, damit keine Doku entsteht, die NUR aus dem Satz besteht.
        return text, STATUS_NO_NAME

    # Satzende sicherstellen, damit der Hinweis nicht an einen offenen Satz
    # anschliesst (Hard-Cap kann mitten im Satz gekappt haben).
    if base[-1] not in ".!?":
        base += "."

    logger.info("suizidalitaet: Standardsatz ergaenzt (%s)", satz)
    return f"{base}\n\n{satz}", STATUS_APPENDED


# Regex fuer den Standardsatz - fuer Tests und fuer den Repair-Pfad (S5),
# der pruefen muss, ob das LLM den Satz weggeschrieben hat.
FALLBACK_RE = re.compile(
    r"ist glaubhaft absprachef(?:ä|ae)hig,\s*keine Anzeichen von akuter "
    r"Suizidalit(?:ä|ae)t",
    re.IGNORECASE,
)
