"""
fallformel.py — Stage 1b: Fallformel fuer den thematischen Entlassbericht (v19.28, S2).

Warum es diese Zwischenstufe gibt:
  Der thematische Entlassbericht (Auftrag -> zentrales Muster -> Prozess-
  fortschritte je Modalitaet -> Reflexion/Symptomveraenderung -> Empfehlungen)
  verlangt vom Modell eine Abstraktion ueber 50-80 Sitzungen: WAS ist das
  Muster? Das ist die eigentlich schwere Aufgabe. Stage 1b trennt sie vom
  Schreiben: aus Stage-1-Summary + Antragsvorlage (+ Prozessreflexion)
  entsteht eine kurze, strukturierte FALLFORMEL mit 1-3 Themenkandidaten,
  je mit Sitzungsbelegen. Stage 2 muss das Thema dann nur noch entfalten,
  nicht mehr finden. Die Therapeut:in sieht die Fallformel im Ergebnis,
  waehlt/editiert sie (D1=B, D2: max. 3 Themen) und kann mit der
  angepassten Fassung neu generieren (Form-Feld `fallformel` -> Stage 1b
  wird uebersprungen).

S0-Befund 2026-09-22 (EB-FrauM/HerrR, gemma4:31b): mit handgemachter
Fallformel im Fokus-Feld war die thematische Variante in beiden Faellen
klar besser (Struktur, Wendepunkte, Empfehlungen) - siehe Sprintplan v19.28.

Regeln (Pflichtkern, nicht editierbar):
  - Quellentreue: jede Aussage mit Datums-/Sitzungsbezug in Klammern
  - Themen = MUSTER (z.B. "Selbstwert verstrickt mit Leistung"), keine
    Symptomlisten und keine Diagnosen
  - 1-3 Kandidaten, nach Tragfaehigkeit (Anzahl der Belege) geordnet;
    im Zweifel weniger
  - Wendepunkte je Modalitaet getrennt (Einzel / Gruppe / Nonverbal) -
    das Material fuer Teil 3 des Berichts (S0: Elternbesuch-Wendepunkt
    ging verloren, weil er nicht der Modalitaet zugeordnet war)
  - Keine Testwerte (die stehen in der Antragsvorlage und gehen direkt in
    Stage 2 - Zahlen-Doppelung = Halluzinationsrisiko)
  - Fehlt ein durchgaengiges Muster: das explizit sagen (kein Erfinden)
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

from app.services.summary_runner import (
    anti_think_suffix,
    source_block,
    stage1_generate,
    wrap_no_think,
)
from app.services.verlauf_summary import detect_summary_hallucination_signals

logger = logging.getLogger(__name__)

# Struktur-Schalter des Entlassberichts (D5). Default = Status quo.
EB_STRUKTUR_MODALITAET = "modalitaet"
EB_STRUKTUR_THEMATISCH = "thematisch"
EB_STRUKTUREN = (EB_STRUKTUR_MODALITAET, EB_STRUKTUR_THEMATISCH)
MAX_THEMEN = 3


def normalize_eb_struktur(value: Optional[str]) -> str:
    """Form-Feld -> gueltiger Schalterwert. Alles Unbekannte = Status quo."""
    v = (value or "").strip().lower()
    return EB_STRUKTUR_THEMATISCH if v == EB_STRUKTUR_THEMATISCH else EB_STRUKTUR_MODALITAET


# ── Prompt ───────────────────────────────────────────────────────────────────

SECTION_AUFTRAG = "### Auftrag"
SECTION_THEMEN = "### Themenkandidaten"
SECTION_WENDEPUNKTE = "### Wendepunkte je Modalität"
SECTION_SYMPTOM = "### Symptomveränderung"
SECTION_OFFEN = "### Offene Themen"
SECTIONS = (SECTION_AUFTRAG, SECTION_THEMEN, SECTION_WENDEPUNKTE, SECTION_SYMPTOM, SECTION_OFFEN)

KEIN_MUSTER_SATZ = "Kein durchgängiges Muster dokumentiert."

FALLFORMEL_SYSTEM_PROMPT = f"""Du bist ein klinisches Analyse-System für psychotherapeutische Verlaufsdokumentation.

DEINE EINZIGE AUFGABE: Erstelle aus der verdichteten Verlaufsdokumentation
(und der Antragsvorlage) eine FALLFORMEL - das Gerüst für einen thematisch
aufgebauten Entlassbericht. Du schreibst NICHT den Bericht. Du formulierst
knapp, belegt und ohne Ausschmückung.

ABSOLUTE REGELN:
1. QUELLENTREUE: Nur was in den Quellen steht. Jede inhaltliche Aussage
   bekommt in Klammern den Beleg: Datum und Sitzungstyp, z.B.
   "(Einzel 23.12.)", "(Kunsttherapie 08.01.)", "(Bezugsgruppe 06.01.)".
   Keine Aussage ohne Beleg. Im Zweifel weglassen.
2. THEMEN SIND MUSTER, KEINE SYMPTOME: Ein Thema beschreibt einen
   Sinnzusammenhang, der sich durch den Verlauf zieht (z.B. "Selbstwert
   verstrickt mit Leistung", "Angst als alter Schutz", "Anpassung vs.
   eigene Bedürfnisse"). "Depression" oder "Schlafstörung" sind KEINE
   Themen. Nutze vorrangig die im Protokoll DOKUMENTIERTEN Hypothesen
   (Abschnitt "Dokumentierte Hypothesen und Muster" der Verdichtung).
3. WENIGER IST MEHR: 1 bis maximal {MAX_THEMEN} Themenkandidaten, geordnet
   nach Tragfähigkeit (wie viele Sitzungen belegen das Muster). Ein
   Kandidat braucht mindestens ZWEI Belege aus verschiedenen Sitzungen.
   Trägt kein Muster: schreibe unter Themenkandidaten nur den Satz
   "{KEIN_MUSTER_SATZ}"
4. WENDEPUNKTE JE MODALITÄT GETRENNT: Für Einzeltherapie, Gruppentherapie
   und nonverbale Therapien (Kunst-, Musik-, Körperarbeit) jeweils die
   konkreten Wendepunkte mit Datum. Eine Modalität ohne dokumentierten
   Wendepunkt: "keine Wendepunkte dokumentiert" - nichts erfinden.
5. KEINE TESTWERTE, KEINE DIAGNOSEN, KEINE MEDIKATION: die stehen in der
   Antragsvorlage und werden dort übernommen. Symptomveränderung nur
   beschreibend laut Verlaufsdokumentation.
6. KEINE EIGENEN DEUTUNGEN über die dokumentierten Hypothesen hinaus.
7. KEINE ANFÜHRUNGSZEICHEN um erfundene Zitate; wörtliche Begriffe des
   Klienten (benannte Anteile wie "Türsteher") dürfen übernommen werden,
   wenn sie so in der Quelle stehen.

FORMAT (genau diese fünf Überschriften, Markdown, insgesamt 200-400 Wörter):

{SECTION_AUFTRAG}
Anliegen und Veränderungswunsch des Klienten/der Klientin zu Beginn, in
seinen/ihren Worten laut Aufnahmegespräch bzw. erster Auftragsklärung,
mit Beleg. 2-4 Sätze.

{SECTION_THEMEN}
1. **Kurztitel des Musters** – 1-2 Sätze, was das Muster ist und woher es
   laut Protokoll kommt (Schutzfunktion, biographischer Kontext). Belege:
   (Einzel 23.12.), (Einzel 30.12.), (Gruppe non-verbal 14.01.)
2. ... (optional, höchstens {MAX_THEMEN} Einträge)

{SECTION_WENDEPUNKTE}
- Einzeltherapie: ...
- Gruppentherapie: ...
- Nonverbale Therapien: ...

{SECTION_SYMPTOM}
2-3 Sätze: Ausgangszustand -> Endzustand laut Verlaufsdokumentation,
inkl. ungünstiger oder gleichbleibender Befunde, mit Beleg.

{SECTION_OFFEN}
1-3 Sätze: was laut Protokoll offen bleibt bzw. als Wunsch für die
Weiterarbeit genannt wurde, mit Beleg.
""" + anti_think_suffix("Fallformel", f"Beginne sofort mit '{SECTION_AUFTRAG}'.")


def _user_content(
    *,
    verlauf_text: str,
    antragsvorlage_text: Optional[str],
    prozessreflexion_text: Optional[str],
    patient_initial: Optional[str],
) -> str:
    body = source_block(label="Verdichtete Verlaufsdokumentation", tag="VERLAUF",
                        text=verlauf_text, patient_initial=patient_initial,
                        workflow="entlassbericht")
    if antragsvorlage_text and antragsvorlage_text.strip():
        body += source_block(label="Antragsvorlage (Anamnese, Aufnahmebefund - nur für den Auftrag)",
                             tag="ANTRAGSVORLAGE", text=antragsvorlage_text.strip())
    if prozessreflexion_text and prozessreflexion_text.strip():
        body += source_block(label="Prozessreflexion des Klienten (Abschluss)",
                             tag="PROZESSREFLEXION", text=prozessreflexion_text.strip())
    body += "Erstelle jetzt die Fallformel in den fünf vorgegebenen Abschnitten."
    return wrap_no_think(body)


# ── Parsing / Auswahl ────────────────────────────────────────────────────────

_H_RE = re.compile(r"^\s*#{2,4}\s*(.+?)\s*$", re.MULTILINE)
_ITEM_RE = re.compile(r"^\s*(\d+)[.)]\s+(.*)$")


def split_sections(text: str) -> dict[str, str]:
    """{'Auftrag': body, 'Themenkandidaten': body, ...} - Schluessel ohne '### '."""
    out: dict[str, str] = {}
    if not text:
        return out
    matches = list(_H_RE.finditer(text))
    for i, m in enumerate(matches):
        key = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[key] = text[start:end].strip()
    return out


def parse_themenkandidaten(text: str) -> list[str]:
    """Nummerierte Eintraege unter '### Themenkandidaten' (mehrzeilig zusammengefasst).
    Leere Liste, wenn das Modell 'Kein durchgängiges Muster dokumentiert.' schreibt."""
    sec = split_sections(text).get(SECTION_THEMEN.lstrip("# ").strip(), "")
    if not sec or KEIN_MUSTER_SATZ.lower() in sec.lower():
        return []
    items: list[str] = []
    cur: list[str] = []
    for line in sec.splitlines():
        m = _ITEM_RE.match(line)
        if m:
            if cur:
                items.append(" ".join(cur).strip())
            cur = [m.group(2).strip()]
        elif line.strip() and cur:
            cur.append(line.strip())
    if cur:
        items.append(" ".join(cur).strip())
    return [i for i in items if i]


def select_themen(text: str, selected: Optional[list[int]]) -> str:
    """Reduziert die Themenkandidaten auf die gewaehlten Indizes (0-basiert,
    max. MAX_THEMEN); None = alle behalten (bereits auf MAX_THEMEN begrenzt).
    Gibt die Fallformel als neuen Text zurueck, Rest unveraendert."""
    items = parse_themenkandidaten(text)
    if not items:
        return text
    if selected is None:
        keep = list(range(min(len(items), MAX_THEMEN)))
    else:
        keep = [i for i in selected if 0 <= i < len(items)][:MAX_THEMEN]
        if not keep:
            keep = [0]
    new_items = [f"{n + 1}. {items[i]}" for n, i in enumerate(keep)]
    sections = split_sections(text)
    sections[SECTION_THEMEN.lstrip("# ").strip()] = "\n".join(new_items)
    order = [s.lstrip("# ").strip() for s in SECTIONS]
    parts = []
    for key in order:
        if key in sections:
            parts.append(f"### {key}\n{sections[key]}".rstrip())
    # unbekannte Abschnitte hinten anhaengen (Modell hat etwas erfunden -> nicht verlieren)
    for key, body in sections.items():
        if key not in order:
            parts.append(f"### {key}\n{body}".rstrip())
    return "\n\n".join(parts).strip()


# ── Halluzinations-/Struktur-Check ───────────────────────────────────────────

_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(?:\d{2,4})?(?!\d)")


def detect_fallformel_issues(text: str, source_text: str) -> list[dict]:
    """Struktur (5 Abschnitte, <= MAX_THEMEN Kandidaten) + Verfahrens-/Zitat-
    Signale (wiederverwendet aus Stage 1) + Datumsbelege, die es in der
    Quelle nicht gibt."""
    issues: list[dict] = []
    if not text or not text.strip():
        return [{"type": "leer", "severity": "critical", "detail": "Fallformel leer"}]
    sections = split_sections(text)
    for s in SECTIONS:
        if s.lstrip("# ").strip() not in sections:
            issues.append({"type": "abschnitt_fehlt", "severity": "high",
                           "detail": f"Abschnitt '{s}' fehlt"})
    n = len(parse_themenkandidaten(text))
    if n > MAX_THEMEN:
        issues.append({"type": "zu_viele_themen", "severity": "medium",
                       "detail": f"{n} Themenkandidaten (max. {MAX_THEMEN}) - wird auf {MAX_THEMEN} gekuerzt"})
    issues.extend(detect_summary_hallucination_signals(text, source_text))
    if source_text:
        src_dates = {(int(d), int(m)) for d, m in _DATE_RE.findall(source_text)}
        bad = sorted({(int(d), int(m)) for d, m in _DATE_RE.findall(text)} - src_dates)
        if bad and src_dates:
            issues.append({
                "type": "datum_ohne_quelle", "severity": "high",
                "detail": "Datumsbelege ohne Entsprechung in der Quelle: "
                          + ", ".join(f"{d:02d}.{m:02d}." for d, m in bad[:8]),
            })
    return issues


# ── Hauptfunktion ────────────────────────────────────────────────────────────

_TEMPERATURE = 0.3
_MAX_TOKENS = 1400


async def build_fallformel(
    *,
    verlauf_text: str,
    antragsvorlage_text: Optional[str] = None,
    prozessreflexion_text: Optional[str] = None,
    patient_initial: Optional[str] = None,
    model: Optional[str] = None,
    raw_source_text: Optional[str] = None,
) -> dict:
    """Ein LLM-Call (kein Retry - die Therapeut:in korrigiert im UI, D1=B).

    raw_source_text: Rohtext fuer den Halluzinationscheck (Verlauf roh +
    Antragsvorlage); None -> verlauf_text + antragsvorlage_text.

    Returns dict: text, system_prompt, user_content, telemetry, issues,
    themen (Liste der Kandidaten), duration_s, degraded.
    """
    if not verlauf_text or not verlauf_text.strip():
        raise RuntimeError("Stage 1b: leerer Verlauf-Text")
    t0 = time.time()
    system_prompt = FALLFORMEL_SYSTEM_PROMPT
    user_content = _user_content(
        verlauf_text=verlauf_text, antragsvorlage_text=antragsvorlage_text,
        prozessreflexion_text=prozessreflexion_text, patient_initial=patient_initial,
    )
    result = await stage1_generate(
        system_prompt, user_content, max_tokens=_MAX_TOKENS, temperature=_TEMPERATURE, model=model,
    )
    text = (result.get("text") or "").strip()
    if not text:
        raise RuntimeError("Stage 1b: Fallformel leer")
    check_source = raw_source_text if raw_source_text else "\n".join(
        t for t in (verlauf_text, antragsvorlage_text or "", prozessreflexion_text or "") if t
    )
    issues = detect_fallformel_issues(text, check_source)
    # D2: hart auf MAX_THEMEN kuerzen (Modell hat evtl. mehr geliefert)
    text = select_themen(text, None)
    themen = parse_themenkandidaten(text)
    degraded = any(i["severity"] in ("critical", "high") for i in issues)
    if degraded:
        logger.warning("Stage 1b Fallformel mit Signalen: %s", issues)
    return {
        "text": text,
        "system_prompt": system_prompt,
        "user_content": user_content,
        "telemetry": result.get("telemetry", {}),
        "issues": issues,
        "themen": themen,
        "duration_s": round(time.time() - t0, 1),
        "degraded": degraded,
        "word_count": len(text.split()),
    }


def fallformel_prompt_block(text: str) -> str:
    """Block fuer den User-Content von Stage 2 (build_user_content)."""
    return (
        "FALLFORMEL (Gerüst des Berichts - von der Therapeutin/dem Therapeuten "
        "bestätigt; Themenkandidaten = die zentralen Muster, Wendepunkte = das "
        "Material für die Modalitätsabsätze):\n"
        f"{text.strip()}\n\n"
        "EINBAU DER FALLFORMEL - VERBINDLICH:\n"
        "1. Teil 1 (Auftrag) aus 'Auftrag'.\n"
        "2. Teil 2 (zentrales Thema) aus den Themenkandidaten - alle genannten, "
        "in dieser Reihenfolge; bei mehreren Themen ihre Verbindung "
        "zueinander herausarbeiten. Hier EINMAL vollständig erklären.\n"
        "3. Teil 3: jeder Modalitätsabsatz greift die Wendepunkte SEINER "
        "Modalität aus 'Wendepunkte je Modalität' auf und bindet sie an das "
        "Thema zurück - ohne das Thema neu herzuleiten.\n"
        "4. Teil 4 aus 'Symptomveränderung' (plus Prozessreflexion und "
        "Testwerte aus den Quellen, sofern vorhanden).\n"
        "5. Teil 5 greift 'Offene Themen' auf.\n"
        "Die Belege in Klammern (Datum/Sitzungstyp) NICHT in den Bericht "
        "übernehmen - sie dienen nur der Orientierung. Steht in der Fallformel "
        f"'{KEIN_MUSTER_SATZ}', schreibe Teil 2 als knappe Beschreibung der "
        "bearbeiteten Themen ohne ein Muster zu konstruieren."
    )
