"""
v19.27: QualityCheck-Regeln fuer Fokus-Treue, Verfahren, Stage-1-Audit und
P1-Struktur. Eigenes Modul, damit quality_check.py nicht weiter waechst;
registriert werden die Regeln dort in CHECK_REGISTRY.

REGELBASIERT, KEIN LLM. Deterministisch, < 100 ms.

Regeln
------
stage1_audit          VERDICHTUNG_DEGRADED (warning), VERDICHTUNG_HALLUZINATION
                      (warning; critical bei ICD), VERDICHTUNG_FALLBACK (info)
doku_length           LENGTH_BELOW_TARGET fuer dokumentation (< 150 Woerter)
doku_struktur         ORGANISATORISCHES_PLATZHALTER (warning),
                      EINLADUNG_GENERISCH (warning, nur mit Quelle),
                      EINLADUNG_FALLBACK (info), DOKU_LISTENFORMAT (warning),
                      ABSCHNITT_DUENN (info)
stichpunkte           MISSING_STICHPUNKT (warning, mit code_detail.fehlend),
                      STICHPUNKTE_IGNORIERT (critical, >= 50 % der Punkte fehlen)
verfahren             VERFAHREN_NICHT_BENANNT (warning),
                      VERFAHREN_PHASE_FEHLT (info - Beobachtung, keine Bewertung)
"""
from __future__ import annotations

import re

from app.services.postprocessing import split_sentences_de
from app.core.workflows import word_limit_for
from app.services.quality_specs import (
    stichpunkt_coverage,
    stichpunkt_present,
    synonyms_for,
)
from app.services.verfahren import VERFAHREN_BY_KEY, fehlende_phasen, verfahren_benannt

# Issue-Codes (werden in quality_check.py re-exportiert und in der
# Registry beansprucht).
ISSUE_CODE_STAGE1_VERDICHTUNG_DEGRADED = "VERDICHTUNG_DEGRADED"
ISSUE_CODE_STAGE1_HALLUZINATION = "VERDICHTUNG_HALLUZINATION"
ISSUE_CODE_STAGE1_FALLBACK = "VERDICHTUNG_FALLBACK"
ISSUE_CODE_ORGANISATORISCHES_PLATZHALTER = "ORGANISATORISCHES_PLATZHALTER"
ISSUE_CODE_EINLADUNG_GENERISCH = "EINLADUNG_GENERISCH"
ISSUE_CODE_EINLADUNG_FALLBACK = "EINLADUNG_FALLBACK"
ISSUE_CODE_DOKU_LISTENFORMAT = "DOKU_LISTENFORMAT"
ISSUE_CODE_ABSCHNITT_DUENN = "ABSCHNITT_DUENN"
ISSUE_CODE_STICHPUNKTE_IGNORIERT = "STICHPUNKTE_IGNORIERT"
ISSUE_CODE_VERFAHREN_NICHT_BENANNT = "VERFAHREN_NICHT_BENANNT"
ISSUE_CODE_VERFAHREN_PHASE_FEHLT = "VERFAHREN_PHASE_FEHLT"

SEVERITY_CRITICAL = "critical"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

# Abschnitte der Gespraechsdokumentation (Reihenfolge wie im Prompt).
DOKU_SECTIONS: tuple[str, ...] = (
    "Auftragsklärung",
    "Relevante Gesprächsinhalte",
    "Hypothesen und Entwicklungsperspektiven",
    "Einladungen",
    "Organisatorisches",
)
# Abschnitte, bei denen Kuerze korrekt ist (Prompt: "hier ist Kuerze korrekt").
_KURZ_ERLAUBT = frozenset({"Einladungen", "Organisatorisches"})

_ORGA_PLATZHALTER_RE = re.compile(
    r"(keine\s+(weiteren\s+|besonderen\s+)?organisatorischen|nicht\s+besprochen"
    r"|entf[äa]llt|keine\s+organisatorischen\s+(punkte|absprachen))",
    re.IGNORECASE,
)
_ORGA_MIN_WORDS = 12

_EINLADUNG_FALLBACK_RE = re.compile(
    r"keine\s+konkrete\s+einladung\s+oder\s+aufgabe\s+vereinbart", re.IGNORECASE,
)
# Vom Prompt verbotene Standard-Hausaufgaben (nur relevant, wenn nicht in
# der Quelle belegt).
_GENERISCHE_AUFGABEN: tuple[tuple[str, str], ...] = (
    ("tagebuch", "Tagebuch führen"),
    ("notizbuch", "Notizbuch führen"),
    ("beobachtungen aufschreib", "Beobachtungen aufschreiben"),
    ("beobachtungen notier", "Beobachtungen notieren"),
    ("achtsamkeitsübung", "Achtsamkeitsübungen"),
    ("achtsamkeitsuebung", "Achtsamkeitsübungen"),
)
_LISTEN_ZEILE_RE = re.compile(r"^\s*(?:[-•*–—]\s+|\d+[.)]\s+)\S", re.MULTILINE)


def _issue(code, severity, message, repair_hint, code_detail=None):
    # Lokaler Import: quality_check importiert dieses Modul (Zirkel).
    from app.services.quality_check import QualityIssue
    return QualityIssue(code=code, severity=severity, message=message,
                        repair_hint=repair_hint, code_detail=code_detail)


# ── Abschnitts-Parser ────────────────────────────────────────────────────────

def _heading_re(section: str) -> re.Pattern:
    names = [section] + [s for s in synonyms_for(section) if len(s) > 6]
    alt = "|".join(re.escape(n) for n in names)
    # Ueberschrift = eigene Zeile, optional **fett**, optional Doppelpunkt.
    return re.compile(r"^\s*\**\s*(?:" + alt + r")\s*\**\s*:?\s*$",
                      re.IGNORECASE | re.MULTILINE)


def doku_sections(text: str) -> dict[str, str]:
    """Zerlegt eine Gespraechsdokumentation an ihren Ueberschriften.
    Returns {Abschnitt: Inhalt} nur fuer gefundene Abschnitte."""
    if not text:
        return {}
    positions: list[tuple[int, int, str]] = []
    for sec in DOKU_SECTIONS:
        m = _heading_re(sec).search(text)
        if m:
            positions.append((m.start(), m.end(), sec))
    positions.sort()
    out: dict[str, str] = {}
    for i, (_start, end, sec) in enumerate(positions):
        nxt = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        out[sec] = text[end:nxt].strip()
    return out


# ── Stage-1-Audit ────────────────────────────────────────────────────────────

def check_stage1_audit(stage1_audits: dict | None) -> list:
    """stage1_audits = {"transkript": audit|None, "verlauf": audit|None}."""
    if not stage1_audits:
        return []
    issues: list = []
    for quelle, audit in stage1_audits.items():
        if not isinstance(audit, dict):
            continue
        label = "Transkript" if quelle == "transkript" else "Verlaufsdokumentation"
        applied = bool(audit.get("applied"))
        reason = str(audit.get("fallback_reason") or "")
        if not applied:
            if reason.startswith("exception:"):
                issues.append(_issue(
                    ISSUE_CODE_STAGE1_FALLBACK, SEVERITY_INFO,
                    f"Verdichtung ({label}) fehlgeschlagen - Rohtext wurde verwendet "
                    f"({reason[:120]}).",
                    "Nicht durch Repair behebbar; bei Bedarf Job neu starten.",
                    {"quelle": quelle, "fallback_reason": reason[:200]},
                ))
            continue
        if audit.get("degraded"):
            issues.append(_issue(
                ISSUE_CODE_STAGE1_VERDICHTUNG_DEGRADED, SEVERITY_WARNING,
                f"Zwischenverdichtung ({label}) wurde nur im Toleranzband akzeptiert "
                f"({audit.get('summary_word_count')} von Ziel {audit.get('target_words')} Woertern"
                f"{', Retry' if audit.get('retry_used') else ''}) - Vollstaendigkeit "
                "des Endtexts pruefen.",
                "Pruefe den Text gegen die Quelle auf fehlende Inhalte; ergaenze nur "
                "Belegtes.",
                {"quelle": quelle, "summary_words": audit.get("summary_word_count"),
                 "target_words": audit.get("target_words"),
                 "retry_used": bool(audit.get("retry_used"))},
            ))
        signals = [i for i in (audit.get("issues") or [])
                   if isinstance(i, dict)
                   and i.get("type") in ("verfahren_halluzination", "icd_halluzination")]
        if signals:
            critical = any(i.get("type") == "icd_halluzination" for i in signals)
            details = [str(i.get("detail") or i.get("type")) for i in signals][:6]
            issues.append(_issue(
                ISSUE_CODE_STAGE1_HALLUZINATION,
                SEVERITY_CRITICAL if critical else SEVERITY_WARNING,
                f"Zwischenverdichtung ({label}) nennt Begriffe, die in der Quelle "
                f"nicht vorkommen: {'; '.join(details)}. Im Endtext pruefen.",
                "Entferne Verfahren/Diagnosen, die nicht in den Quellen belegt sind.",
                {"quelle": quelle, "signals": signals[:10]},
            ))
    return issues


# ── P1 Laenge ────────────────────────────────────────────────────────────────

def check_doku_length(text: str, workflow: str) -> list:
    """LENGTH_BELOW_TARGET fuer dokumentation: unter dem unteren Wortlimit
    (150), aber ueber der Stub-Schwelle (50 %, die meldet _check_length)."""
    if workflow != "dokumentation":
        return []
    n = len((text or "").split())
    lo, hi = word_limit_for(workflow, fallback=(150, 450))
    if n == 0 or n >= lo or n < lo * 0.5:
        return []
    return [_issue(
        "LENGTH_BELOW_TARGET", SEVERITY_WARNING,
        f"Gespraechsdokumentation mit {n} Woertern unter dem Zielbereich ({lo}-{hi}).",
        "Abschnitte als zusammenhaengende Absaetze ausformulieren - "
        "AUSSCHLIESSLICH mit Inhalten aus Transkript/Stichpunkten, nichts erfinden.",
        {"actual": n, "min": lo, "max": hi},
    )]


# ── P1 Struktur ──────────────────────────────────────────────────────────────

def check_doku_struktur(text: str, workflow: str, source_text: str = "") -> list:
    if workflow != "dokumentation" or not text:
        return []
    issues: list = []
    secs = doku_sections(text)

    # Organisatorisches: Platzhalter statt Weglassen.
    orga = secs.get("Organisatorisches")
    if orga is not None:
        if len(orga.split()) < _ORGA_MIN_WORDS or _ORGA_PLATZHALTER_RE.search(orga):
            issues.append(_issue(
                ISSUE_CODE_ORGANISATORISCHES_PLATZHALTER, SEVERITY_WARNING,
                "Abschnitt 'Organisatorisches' enthaelt nur einen Platzhalter - "
                "laut Vorgabe ganz weglassen, wenn nichts Organisatorisches besprochen wurde.",
                "Entferne den Abschnitt 'Organisatorisches' samt Ueberschrift, wenn "
                "keine administrativen Absprachen vorkamen.",
                {"inhalt": orga[:160]},
            ))

    # Einladungen: Fallback-Satz (info) / generische Hausaufgabe (warning).
    einl = secs.get("Einladungen")
    if einl is not None:
        if _EINLADUNG_FALLBACK_RE.search(einl):
            issues.append(_issue(
                ISSUE_CODE_EINLADUNG_FALLBACK, SEVERITY_INFO,
                "Einladungen: keine konkrete Einladung oder Aufgabe vereinbart "
                "(Standardsatz).",
                "Nur ergaenzen, wenn im Transkript tatsaechlich eine Einladung "
                "ausgesprochen wurde.",
                None,
            ))
        if source_text:
            src_lo = source_text.lower()
            einl_lo = einl.lower()
            generisch = [label for stem, label in _GENERISCHE_AUFGABEN
                         if stem in einl_lo and stem not in src_lo]
            if generisch:
                issues.append(_issue(
                    ISSUE_CODE_EINLADUNG_GENERISCH, SEVERITY_WARNING,
                    "Einladungen enthalten Standard-Hausaufgaben, die in den Quellen "
                    f"nicht vorkommen: {', '.join(generisch)}.",
                    "Entferne Aufgaben, die der/die Therapeut/in nicht woertlich "
                    "ausgesprochen hat; ggf. Standardsatz 'Es wurde keine konkrete "
                    "Einladung oder Aufgabe vereinbart.'",
                    {"generisch": generisch},
                ))

    # Listenformat im Fliesstext.
    listen = _LISTEN_ZEILE_RE.findall(text)
    if len(listen) >= 2:
        issues.append(_issue(
            ISSUE_CODE_DOKU_LISTENFORMAT, SEVERITY_WARNING,
            f"Dokumentation enthaelt {len(listen)} Aufzaehlungszeilen - Vorgabe ist "
            "Fliesstext pro Abschnitt.",
            "Aufzaehlungen in zusammenhaengende Saetze ueberfuehren, Inhalt unveraendert.",
            {"zeilen": len(listen)},
        ))

    # Duenne Pflichtabschnitte (< 2 Saetze).
    duenn = []
    for sec, inhalt in secs.items():
        if sec in _KURZ_ERLAUBT or not inhalt:
            continue
        n_s = len([s for s in split_sentences_de(inhalt) if s.strip()])
        if n_s < 2:
            duenn.append({"section": sec, "saetze": n_s})
    if duenn:
        issues.append(_issue(
            ISSUE_CODE_ABSCHNITT_DUENN, SEVERITY_INFO,
            "Sehr knappe Abschnitte (< 2 Saetze): "
            + ", ".join(d["section"] for d in duenn) + ".",
            "Abschnitt aus dem vorhandenen Material entfalten - keine Erfindung, "
            "keine Wiederholung.",
            {"abschnitte": duenn},
        ))
    return issues


# ── Stichpunkte / Fokus-Themen ───────────────────────────────────────────────

def check_stichpunkte(text: str, stichpunkte: list | None,
                      stage1_applied: bool = False) -> list:
    """Ersetzt _check_stichpunkte (v19.6): pro fehlendem Punkt eine WARNUNG
    mit code_detail.fehlend; zusaetzlich STICHPUNKTE_IGNORIERT (critical),
    wenn >= 2 Punkte uebergeben wurden und >= 50 % fehlen (D7=A)."""
    if not stichpunkte:
        return []
    from app.services.quality_check import ISSUE_CODE_MISSING_STICHPUNKT
    issues: list = []
    bullets = [(b or "").strip() for b in stichpunkte if (b or "").strip()]
    fehlend_punkte: list[str] = []
    ctx_note = " (Transkript wurde vor der Generierung verdichtet)" if stage1_applied else ""
    for b in bullets:
        if stichpunkt_present(text, b):
            continue
        cov = stichpunkt_coverage(text, b)
        fehlend_punkte.append(b)
        issues.append(_issue(
            ISSUE_CODE_MISSING_STICHPUNKT, SEVERITY_WARNING,
            f"Stichpunkt/Fokus-Thema nicht aufgegriffen: '{b}'"
            + (f" - fehlend: {', '.join(cov['fehlend'])}" if cov["fehlend"] else "")
            + ctx_note + ".",
            f"Greife das Thema '{b}' im Text auf - AUSSCHLIESSLICH sofern es "
            "durch die Quellen (Transkript/Unterlagen) gedeckt ist; ein nicht "
            "belegter Schwerpunkt wird als therapeutische Hypothese formuliert. "
            "Erfinde keine Inhalte, nur um das Stichwort unterzubringen.",
            {"stichpunkt": b, "hits": cov["hits"], "total": cov["total"],
             "fehlend": cov["fehlend"], "akronym_fehlt": cov["akronym_fehlt"]},
        ))
    if len(bullets) >= 2 and len(fehlend_punkte) * 2 >= len(bullets):
        issues.append(_issue(
            ISSUE_CODE_STICHPUNKTE_IGNORIERT, SEVERITY_CRITICAL,
            f"{len(fehlend_punkte)} von {len(bullets)} Zusatzangaben nicht "
            "aufgegriffen - der Themenblock wurde offenbar uebergangen" + ctx_note + ".",
            "Setze die THERAPEUTISCHEN SCHWERPUNKTE verbindlich um: jeder Punkt "
            "wird dem passenden Abschnitt zugeordnet und dort explizit "
            "ausgefuehrt; nicht belegte Punkte als Hypothese formulieren.",
            {"fehlend": fehlend_punkte, "total": len(bullets)},
        ))
    return issues


# ── Verfahren ────────────────────────────────────────────────────────────────

def check_verfahren(text: str, workflow: str, verfahren_keys: list | None) -> list:
    """VERFAHREN_NICHT_BENANNT (warning): in Quellen/Stichpunkten belegtes
    Verfahren fehlt im Output. VERFAHREN_PHASE_FEHLT (info, nur P1):
    Phasen-Marker nicht auffindbar - reine Beobachtung, keine Bewertung."""
    if not verfahren_keys or not text:
        return []
    issues: list = []
    for key in verfahren_keys:
        v = VERFAHREN_BY_KEY.get(str(key))
        if v is None:
            continue
        if not verfahren_benannt(text, v):
            issues.append(_issue(
                ISSUE_CODE_VERFAHREN_NICHT_BENANNT, SEVERITY_WARNING,
                f"In Quellen/Stichpunkten genanntes Verfahren nicht benannt: {v.label}.",
                f"Benenne das Verfahren '{v.label}' namentlich und dokumentiere die "
                "Sitzung entlang seiner Struktur - nur, was tatsaechlich geschah.",
                {"verfahren": v.key, "label": v.label},
            ))
            continue
        if workflow == "dokumentation":
            fehlend = fehlende_phasen(text, v)
            if fehlend:
                phasen = [v.phasen[i - 1] if i - 1 < len(v.phasen) else f"Phase {i}"
                          for i in fehlend]
                issues.append(_issue(
                    ISSUE_CODE_VERFAHREN_PHASE_FEHLT, SEVERITY_INFO,
                    f"{v.key.upper()}: zu {len(fehlend)} Phase(n) keine Marker im Text "
                    "gefunden (Beobachtung - je nach Sitzungsvariante legitim).",
                    "Nur ergaenzen, wenn die Phase im Gespraech tatsaechlich vorkam.",
                    {"verfahren": v.key, "fehlende_phasen": fehlend,
                     "phasen": [p[:90] for p in phasen]},
                ))
    return issues
