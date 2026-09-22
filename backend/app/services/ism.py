"""
ISM-Fragebogen (PX, v19.18) - Domaenenmodul.

Erzeugt aus einem Therapiegespraech (P0-Transkript) einen individualisierten
ISM-Fragebogen fuer das Synergetische Navigationssystem (SNS, G. Schiepek):
Slider-Items (0-100) mit individuellen Pol-Labels, zugeordnet zu den sechs
Faktoren der individualisierten Fragebogenstruktur der sysTelios-
Prozessdiagnostik.

Architektur (Entscheidungen A1-A5, Sprintplan v19.18):
  A1  Das LLM liefert STRIKTES JSON (Ollama format=JSON-Schema, Pattern
      analog Befund-Structured-Output v19.7). Das SNS-XML wird deterministisch
      hier im Backend gerendert - nie vom Modell.
  A2  Der Faktorblock (IDs 0-5, Namen, Beschreibungen) ist eine Konstante,
      1:1 aus der SNS-Referenz (BeispielOutput.xml der Prozessdiagnostik).
  A3  Kein Fliesstext-QC: run_ism_quality_check() ersetzt fuer diesen
      Workflow die Standard-Checks (word_limit/fidelity greifen nicht auf
      JSON). Einstieg via quality_check.run_quality_check-Dispatch.
  A4  changePoles='false' immer - Umpolung geschieht sprachlich (Hindernis-
      Items belastungsseitig ODER auf die Ressourcenseite gedreht).
  A5  Slider 0/100, weights='1.0', positionLocked='false',
      allowComments='false' pro Item; Fragebogen-Level allowComments='true'.

Konsumenten:
  - app/api/jobs.py            -> _run_ism_generation() (Job-Pfad)
  - app/api/ism.py             -> POST /api/ism/xml (Export der editierten
                                  Vorschau aus dem Frontend)
  - app/services/quality_check -> run_ism_quality_check() via Dispatch
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)


# ── A2: Die sechs Faktoren der individualisierten ISM-Struktur ───────────────
# name/beschreibung_xml exakt aus der SNS-Referenz (BeispielOutput.xml);
# beschreibung_prompt ist die operative Kurzfassung fuer das Modell.
ISM_FAKTOREN: tuple[dict, ...] = (
    {
        "id": 0,
        "name": "I Zielerleben",
        "beschreibung_xml": "Zielerleben - Wofür - Kernanliegen; Integrationsidee",
        "beschreibung_prompt": (
            "Zielerleben / Wofür: Das Kernanliegen des Aufenthalts. Woran merkt "
            "die Person, dass sie ihrem Wofür heute einen Schritt näher gekommen ist?"
        ),
    },
    {
        "id": 1,
        "name": "II Ressourcen",
        "beschreibung_xml": (
            "Ressourcen - bezogen auf die Kernanliegen - was trägt am stärksten "
            "dazu bei, was bahnt es am deutlichsten"
        ),
        "beschreibung_prompt": (
            "Ressourcen: Konkrete Fähigkeiten, Erfahrungen oder Beziehungen der "
            "Person, die das Kernanliegen am stärksten bahnen. Heute-Formulierung: "
            "konnte ich diese Ressource nutzen/stärken?"
        ),
    },
    {
        "id": 2,
        "name": "III Hindernisse",
        "beschreibung_xml": (
            "Hindernisse: bezogen auf die Kernanliegen, was scheint mich daran "
            "immer wieder zu hindern, was fühlt sich als Widerstand, Symptom / "
            "Belastungserleben an.\n- in diesem Fall bedeutet hohes Rating hohe "
            "Belastung - alle Fragen, die auf die Ressourcenseite umgedreht wurden "
            "bitte umpolen (zb heute konnte ich mit Kopfweh gut umgehen)"
        ),
        "beschreibung_prompt": (
            "Hindernisse: Was hindert die Person immer wieder (Glaubenssätze, "
            "Muster, Symptome). ZWEI erlaubte Formulierungsrichtungen: "
            "(a) belastungsseitig ('Wie sehr hat X heute noch eine Rolle "
            "gespielt...' - hohes Rating = hohe Belastung) ODER "
            "(b) auf die Ressourcenseite gedreht ('Heute konnte ich mit X gut "
            "umgehen' - hohes Rating = gelungener Umgang)."
        ),
    },
    {
        "id": 3,
        "name": "IV Hilfreiche Auswirkungen",
        "beschreibung_xml": (
            "Hilfreiche Auswirkungen vom Zielerleben: wie verändert sich dann "
            "mein Emotionserleben, meine Einstellungen zu mir selbst, meine "
            "Selbstfürsorge\n"
        ),
        "beschreibung_prompt": (
            "Hilfreiche Auswirkungen / Gewinn: Was wird durch die Entwicklung "
            "möglich - Veränderung im Emotionserleben, in der Einstellung zu "
            "sich selbst, in der Selbstfürsorge."
        ),
    },
    {
        "id": 4,
        "name": "V Herausfordernde Auswirkungen",
        "beschreibung_xml": (
            "Herausfordernde Auswirkungen vom Zielerleben: Preise, die ich zahle, "
            "WEIL ich mein Ziel erreiche, Muster von denen ich mich verabschiede "
            "weil meine Selbstfürsorge wächst\n"
        ),
        "beschreibung_prompt": (
            "Herausfordernde Auswirkungen / Preis: Was wird mit der Entwicklung "
            "nicht mehr so nötig sein wie früher - alte Muster (z.B. "
            "Perfektionismus, Überverausgabung), von denen sich die Person "
            "verabschiedet, obwohl sie bisher Halt gaben."
        ),
    },
    {
        "id": 5,
        "name": "VI Utilisierung",
        "beschreibung_xml": (
            "Nützlichkeit von Symptomen - Utilisierung: wofür kann ich  "
            "Hindernisse nutzen - um mich an welche Ressoucen zu erinnern. Wie "
            "kann ich aus Metaperspektive den Umgang mit dem was mich bisher "
            "hindert so verändern, dass es mich in Kontakt mit Ressourcen "
            "bringt.\n"
        ),
        "beschreibung_prompt": (
            "Utilisierung: Wofür kann die Person Hindernisse oder Symptome als "
            "Signal nutzen - woran erinnern sie, mit welcher Ressource bringen "
            "sie in Kontakt ('Wenn ich wieder X erlebe, erinnert mich das "
            "daran, dass ...')."
        ),
    },
)

ISM_FAKTOR_IDS: frozenset[int] = frozenset(f["id"] for f in ISM_FAKTOREN)
ISM_FAKTOR_BY_ID: dict[int, dict] = {f["id"]: f for f in ISM_FAKTOREN}

# Itemanzahl (D1c): waehlbar 4-12, Default 6 (~1 Item pro Faktor, Luecken
# erlaubt wenn das Gespraech einen Faktor nicht hergibt).
ISM_MIN_ITEMS = 4
ISM_MAX_ITEMS = 12
ISM_DEFAULT_ITEMS = 6


def clamp_n_items(raw: Optional[int]) -> int:
    """Normalisiert die gewuenschte Itemanzahl auf [4, 12]; None -> 6."""
    if raw is None:
        return ISM_DEFAULT_ITEMS
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return ISM_DEFAULT_ITEMS
    return max(ISM_MIN_ITEMS, min(ISM_MAX_ITEMS, n))


# ── Pydantic-Modelle (Validierung des LLM-JSON) ──────────────────────────────

class IsmItem(BaseModel):
    """Ein Slider-Item des Fragebogens."""
    faktor_id: int = Field(ge=0, le=5)
    frage: str = Field(min_length=5)
    pol_min: str = Field(min_length=1)   # Label am linken Pol (Wert 0)
    pol_max: str = Field(min_length=1)   # Label am rechten Pol (Wert 100)


class IsmFragebogen(BaseModel):
    """Vollstaendiger Fragebogen wie vom LLM geliefert bzw. im Frontend
    editiert. `name` wird erst beim XML-Export gesetzt (aus der SNS-Kennung),
    nicht vom Modell."""
    begruessung: str = Field(min_length=3)
    verabschiedung: str = Field(min_length=3)
    items: list[IsmItem] = Field(min_length=1)


# ── Structured-Output-Schema (Ollama format-Parameter) ───────────────────────

def build_ism_json_schema() -> dict:
    """JSON-Schema fuer Ollamas format-Parameter.

    Bewusst OHNE minItems/maxItems: einige Ollama-Grammatik-Backends
    unterstuetzen Array-Kardinalitaeten nicht zuverlaessig - die Itemanzahl
    wird stattdessen im Prompt gefordert und in validate_ism_payload()
    geprueft (Pattern: Constraint im Prompt, Verifikation im Code).
    """
    return {
        "type": "object",
        "properties": {
            "begruessung": {"type": "string"},
            "verabschiedung": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "faktor_id": {"type": "integer"},
                        "frage": {"type": "string"},
                        "pol_min": {"type": "string"},
                        "pol_max": {"type": "string"},
                    },
                    "required": ["faktor_id", "frage", "pol_min", "pol_max"],
                },
            },
        },
        "required": ["begruessung", "verabschiedung", "items"],
    }


# ── Prompt-Bausteine ─────────────────────────────────────────────────────────

# Destillierte Few-Shots aus den Klinik-Beispielen der Prozessdiagnostik
# (HM57347_ISM-FB / ISM Fragebogen_Vorschlaege) - zeigen Frageform UND
# Pol-Label-Charakteristik, bewusst als JSON damit die Zielform doppelt
# verankert ist. Inhalte sind anonymisierte Muster, keine Patientendaten.
ISM_FEW_SHOT = """\
BEISPIEL-ITEMS (zeigen Form, Tonfall und Pol-Logik - NICHT die Inhalte übernehmen):

{"faktor_id": 0, "frage": "Heute bin ich einen Schritt näher gekommen, mit meiner inneren Unruhe umgehen zu können.", "pol_min": "Schade. Morgen kann's nur besser werden.", "pol_max": "Bingo!"}
{"faktor_id": 1, "frage": "Heute konnte ich meine Fähigkeit, geduldig und großherzig zu sein, auch für mich selbst nutzen.", "pol_min": "noch bekommen das eher die Anderen von mir - daran lerne ich, wie es geht", "pol_max": "...um für mich selbst da zu sein"}
{"faktor_id": 2, "frage": "Wie sehr hat der alte Glaubenssatz, alles richtig machen zu müssen, heute noch eine Rolle gespielt?", "pol_min": "ich kann meinen Selbstwert spüren, ohne mich dafür anstrengen zu müssen", "pol_max": "...als wäre ich nur richtig, wenn ich Leistung bringe"}
{"faktor_id": 3, "frage": "Mir gelingt es mehr und mehr, unabhängig vom Außen meine Bedürfnisse anzuerkennen.", "pol_min": "Ich übe noch", "pol_max": "Geht bereits ganz gut"}
{"faktor_id": 4, "frage": "Heute kann ich mir vorstellen, mit weniger Perfektionismus auszukommen.", "pol_min": "gerade hilft er mir noch, mich zu stabilisieren", "pol_max": "ich brauche das nicht mehr so - ich spüre meinen Selbstwert"}
{"faktor_id": 5, "frage": "Inwiefern konnten meine körperlichen Signale heute ein Hinweis auf größeren Selbstfürsorgebedarf sein?", "pol_min": "heute gab es keine Signale - und das ist ok", "pol_max": "ich habe sie bemerkt und für mich genutzt"}
"""


def _render_faktoren_prompt() -> str:
    lines = []
    for f in ISM_FAKTOREN:
        lines.append(f"Faktor {f['id']} ({f['name']}): {f['beschreibung_prompt']}")
    return "\n".join(lines)


def build_ism_system_prompt(
    workflow_instructions: Optional[str],
    n_items: int,
) -> str:
    """System-Prompt fuer die ISM-Generierung.

    Bewusst NICHT ueber build_system_prompt (Pattern
    build_befund_structured_prompt): Laengenanker, Stilschablonen und der
    "Schreibe jetzt den Bericht"-Schluss passen nicht auf JSON-Ausgabe.
    Der BASE_PROMPTS-Kernel und die (editierbaren) Workflow-Anweisungen
    werden hier direkt komponiert.
    """
    # Lazy-Import: prompts importiert nichts aus ism -> kein Zyklus, aber
    # symmetrisch zum restlichen Codebase-Stil gehalten.
    from app.services.prompts import BASE_PROMPTS, WORKFLOW_INSTRUCTIONS_DEFAULT

    instructions = (
        workflow_instructions.strip()
        if workflow_instructions and workflow_instructions.strip()
        else WORKFLOW_INSTRUCTIONS_DEFAULT.get("ism_fragebogen", "")
    )
    kernel = BASE_PROMPTS.get("ism_fragebogen", "")

    parts = [
        "Du bist Prozessdiagnostik-Assistent der sysTelios Klinik für "
        "Psychosomatik und Psychotherapie (hypnosystemisch-ressourcenorientierter "
        "Ansatz). Du erstellst aus einem Therapiegespräch einen "
        "individualisierten ISM-Fragebogen für das tägliche Prozessmonitoring "
        "(SNS, Synergetisches Navigationssystem).",
        "\nAUFTRAG / INHALTLICHE ANWEISUNGEN:\n" + instructions,
        "\nDIE SECHS FAKTOREN (jedes Item wird GENAU EINEM Faktor per "
        "faktor_id zugeordnet):\n" + _render_faktoren_prompt(),
        "\n" + kernel,
        "\n" + ISM_FEW_SHOT,
        f"\nITEMANZAHL: Erstelle GENAU {n_items} Items. Verteile sie über die "
        "Faktoren entlang der Gesprächsinhalte - beginne bei Faktor 0 "
        "(Zielerleben) und decke möglichst viele Faktoren ab. Ein Faktor darf "
        "leer bleiben, wenn das Gespräch dafür keinen tragfähigen Inhalt "
        "liefert; erfinde NIEMALS Inhalte, um einen Faktor zu füllen.",
        "\nAntworte AUSSCHLIESSLICH mit dem JSON-Objekt "
        '({"begruessung": ..., "verabschiedung": ..., "items": [...]}) - '
        "keine Erklärungen, kein Markdown.",
    ]
    return "\n".join(parts)


def build_ism_user_content(
    transcript: str,
    themen: Optional[str] = None,
) -> str:
    """User-Content: Transkript (Pflichtquelle) + optionale Themen-Stichpunkte."""
    parts = [f"THERAPIEGESPRÄCH (Transkript):\n{transcript.strip()}"]
    if themen and themen.strip():
        parts.append(
            "\nEINZELNE FRAGEN / THEMEN (vom Therapeuten gewünschte "
            "Schwerpunkte - nur aufgreifen, was im Gespräch belegt ist; "
            "explizit formulierte Frage-Wünsche dürfen als Item übernommen "
            "und in die Zielform gebracht werden):\n" + themen.strip()
        )
    parts.append(
        "\nErstelle jetzt den individualisierten ISM-Fragebogen als JSON."
    )
    return "\n\n".join(parts)


# ── Validierung + strukturelle Qualitaetspruefung ────────────────────────────

_WIR_FORM_RE = re.compile(r"\bwir\b|\buns(?:er\w*)?\b", re.IGNORECASE)


def validate_ism_payload(
    data: Any,
    n_items_requested: int,
) -> tuple[Optional[IsmFragebogen], list[str]]:
    """Validiert das LLM-JSON.

    Returns (fragebogen, hard_errors):
      fragebogen  - IsmFragebogen wenn strukturell valide, sonst None
      hard_errors - Liste menschenlesbarer Fehler (leer = valide).
                    Weiche Auffaelligkeiten (Itemanzahl-Abweichung, Wir-Form)
                    sind KEINE hard_errors - die meldet der QualityCheck.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return None, [f"LLM-Antwort ist kein JSON-Objekt (Typ: {type(data).__name__})"]
    try:
        fb = IsmFragebogen.model_validate(data)
    except ValidationError as e:
        for err in e.errors()[:5]:
            loc = ".".join(str(p) for p in err.get("loc", ()))
            errors.append(f"{loc}: {err.get('msg')}")
        return None, errors

    for i, item in enumerate(fb.items):
        if item.faktor_id not in ISM_FAKTOR_IDS:
            errors.append(
                f"Item {i + 1}: unbekannte faktor_id {item.faktor_id} "
                f"(erlaubt: 0-5)"
            )
    if errors:
        return None, errors
    return fb, []


# ── v19.29: Regelkatalog des ISM-QC ──────────────────────────────────────────
# Jede Regel ist eine Funktion fb -> list[QualityIssue]; ISM_CHECKS ist der
# Katalog in Ausfuehrungsreihenfolge (analog quality_check.CHECK_REGISTRY).
# len(ISM_CHECKS) liefert `checks_run` fuer die Status-Meldung im Frontend.
# Die Regeln laufen sowohl nach der Generierung (run_job -> quality_check-
# Dispatch) als auch live auf dem editierten Stand (POST /api/ism/check).

# D2: Schwellen als Konstanten - anpassen, falls das SNS ein anderes
# Label-Limit hat. ISM_FRAGE_MIN_CHARS liegt bewusst ueber dem Pydantic-
# min_length=5 (das faengt nur Leerstrings ab): eine Selbstauskunft unter
# 12 Zeichen ist beim Editieren stehen geblieben ("Heute ...").
ISM_FRAGE_MIN_CHARS = 12
ISM_POL_MAX_CHARS = 60


def _qi(code, severity, message, repair_hint, code_detail=None):
    from app.services.quality_check import QualityIssue
    return QualityIssue(code=code, severity=severity, message=message,
                        repair_hint=repair_hint, code_detail=code_detail)


def _rule_pole_identisch(fb: IsmFragebogen) -> list:
    """Leere oder identische Pole machen den Slider unbrauchbar."""
    issues = []
    for i, item in enumerate(fb.items):
        if item.pol_min.strip().lower() == item.pol_max.strip().lower():
            issues.append(_qi(
                "ISM_POLE_IDENTISCH", "warning",
                f"Item {i + 1}: min- und max-Label sind identisch "
                f"('{item.pol_min[:60]}') - der Slider hat keine Richtung.",
                "Formuliere zwei unterscheidbare Pol-Labels: links "
                "validierend/einladend (Wert 0), rechts "
                "ressourcenbestätigend (Wert 100).",
                {"item_index": i, "frage": item.frage[:120]},
            ))
    return issues


def _rule_wir_form(fb: IsmFragebogen) -> list:
    """Items sind Selbstauskuenfte des Klienten in Ich-Perspektive."""
    issues = []
    for i, item in enumerate(fb.items):
        joined = f"{item.frage} {item.pol_min} {item.pol_max}"
        if _WIR_FORM_RE.search(joined):
            issues.append(_qi(
                "ISM_WIR_FORM", "warning",
                f"Item {i + 1} enthält Wir-/Uns-Formulierungen - "
                "ISM-Items sind Selbstauskünfte in Ich-Perspektive.",
                "Formuliere das Item in der Ich-Perspektive des Klienten "
                "('Heute konnte ich ...'), ohne Wir-Form.",
                {"item_index": i, "frage": item.frage[:120]},
            ))
    return issues


def _rule_item_duplikat(fb: IsmFragebogen) -> list:
    issues = []
    seen: dict[str, int] = {}
    for i, item in enumerate(fb.items):
        key = item.frage.strip().lower()
        if key in seen:
            issues.append(_qi(
                "ISM_ITEM_DUPLIKAT", "warning",
                f"Item {i + 1} ist ein Duplikat von Item {seen[key] + 1}.",
                "Ersetze das Duplikat durch ein eigenständiges Item.",
                {"item_index": i, "duplicate_of": seen[key]},
            ))
        else:
            seen[key] = i
    return issues


def _rule_frage_kurz(fb: IsmFragebogen) -> list:
    """v19.29 (D2): beim Editieren entstandene Rumpf-Fragen."""
    issues = []
    for i, item in enumerate(fb.items):
        if len(item.frage.strip()) < ISM_FRAGE_MIN_CHARS:
            issues.append(_qi(
                "ISM_FRAGE_KURZ", "warning",
                f"Item {i + 1}: Frage ist leer oder zu kurz ('{item.frage.strip()[:40]}').",
                "Formuliere eine vollständige Selbstauskunft in Ich-Perspektive.",
                {"item_index": i, "frage": item.frage[:120]},
            ))
    return issues


def _rule_pol_zu_lang(fb: IsmFragebogen) -> list:
    """v19.29 (D2): Pol-Labels sind Slider-Beschriftungen - lange Labels
    werden im SNS abgeschnitten oder umbrochen. Info."""
    issues = []
    for i, item in enumerate(fb.items):
        lang = [lbl for lbl in (item.pol_min, item.pol_max) if len(lbl.strip()) > ISM_POL_MAX_CHARS]
        if lang:
            issues.append(_qi(
                "ISM_POL_ZU_LANG", "info",
                f"Item {i + 1}: Pol-Label länger als {ISM_POL_MAX_CHARS} Zeichen "
                f"({max(len(x.strip()) for x in lang)}) - im SNS-Slider ggf. abgeschnitten.",
                "Pol-Label kürzen; die Aussage gehört in die Frage.",
                {"item_index": i, "max_chars": ISM_POL_MAX_CHARS},
            ))
    return issues


def _rule_faktor_unbesetzt(fb: IsmFragebogen) -> list:
    """Rein informativ (leere Faktoren sind erlaubt, D1c)."""
    covered = sorted({it.faktor_id for it in fb.items})
    uncovered = sorted(ISM_FAKTOR_IDS - set(covered))
    if not uncovered:
        return []
    namen = ", ".join(ISM_FAKTOR_BY_ID[i]["name"] for i in uncovered)
    return [_qi(
        "ISM_FAKTOR_UNBESETZT", "info",
        f"Ohne Items geblieben: {namen}. Zulässig, wenn das Gespräch "
        "dafür keinen tragfähigen Inhalt liefert.",
        "Nur ergänzen, wenn das Gespräch belegbaren Inhalt für diese "
        "Faktoren enthält - nichts erfinden.",
        {"uncovered_factor_ids": uncovered},
    )]


# (name, funktion, codes) - json_valid ist die implizite erste Regel
# (run_ism_quality_check / ism_issues_for_payload), zaehlt aber mit.
ISM_CHECKS: tuple[tuple[str, Any, tuple[str, ...]], ...] = (
    ("json_valid", None, ("ISM_JSON_INVALID",)),
    ("pole_identisch", _rule_pole_identisch, ("ISM_POLE_IDENTISCH",)),
    ("wir_form", _rule_wir_form, ("ISM_WIR_FORM",)),
    ("item_duplikat", _rule_item_duplikat, ("ISM_ITEM_DUPLIKAT",)),
    ("frage_kurz", _rule_frage_kurz, ("ISM_FRAGE_KURZ",)),
    ("pol_zu_lang", _rule_pol_zu_lang, ("ISM_POL_ZU_LANG",)),
    ("faktor_unbesetzt", _rule_faktor_unbesetzt, ("ISM_FAKTOR_UNBESETZT",)),
)
ISM_CHECKS_RUN = len(ISM_CHECKS)


def _json_invalid_issue(detail: str, *, fields: Optional[list] = None):
    return _qi(
        "ISM_JSON_INVALID", "critical",
        "Der Fragebogen konnte nicht als valides JSON gelesen werden - "
        "Vorschau und XML-Export sind nicht möglich.",
        "Gib den Fragebogen als valides JSON-Objekt mit den Feldern "
        "begruessung, verabschiedung und items zurück.",
        {"error": detail[:300], **({"fields": fields} if fields else {})},
    )


def run_ism_checks(fb: IsmFragebogen) -> list:
    """Alle Regeln aus ISM_CHECKS auf einem validen Fragebogen."""
    issues: list = []
    for _name, fn, _codes in ISM_CHECKS:
        if fn is not None:
            issues.extend(fn(fb))
    return issues


def ism_issues_for_payload(data: Any) -> list:
    """v19.29: QC fuer ein (ggf. im Frontend editiertes) Fragebogen-Dict.
    Strukturfehler werden als ISM_JSON_INVALID-Issue gemeldet (mit den
    Pydantic-Feldfehlern in code_detail.fields), nicht als Exception -
    der Live-Check darf beim Editieren nicht abbrechen (D1=A)."""
    if not isinstance(data, dict):
        return [_json_invalid_issue(f"kein Objekt (Typ: {type(data).__name__})")]
    try:
        fb = IsmFragebogen.model_validate(data)
    except ValidationError as e:
        fields = [
            {"loc": ".".join(str(p) for p in err.get("loc", ())), "msg": str(err.get("msg"))}
            for err in e.errors()[:8]
        ]
        return [_json_invalid_issue("; ".join(f"{f['loc']}: {f['msg']}" for f in fields), fields=fields)]
    return run_ism_checks(fb)


def run_ism_quality_check(text: str) -> list:
    """A3: Struktureller QC fuer den ISM-Workflow (ersetzt die Fliesstext-
    Checks). `text` ist das persistierte result_text (JSON-String).
    Rueckgabe: list[QualityIssue] (Import lazy - quality_check importiert
    dieses Modul, wir vermeiden den Zyklus zur Modul-Ladezeit).
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return [_json_invalid_issue(str(e))]
    return ism_issues_for_payload(data)


# ── A1: Deterministischer SNS-XML-Renderer ───────────────────────────────────

# Fixtexte (Struktur wie SNS-Referenz BeispielOutput.xml). instruction ist
# der generische Tagebuch-Hinweis der Prozessdiagnostik.
ISM_XML_INSTRUCTION = (
    "Bitte hier zumindest stichwortartig Tagebuchnotizen hinterlassen, mit "
    "dem Ziel, dass Sie selbst ihren Tag wieder erkennen..."
)
ISM_XML_FACTOR_COLOR = "#0A4B71"


def _cdata(text: str) -> str:
    """CDATA-sicheres Wrapping: ']]>' im Inhalt wird gesplittet."""
    safe = (text or "").replace("]]>", "]]]]><![CDATA[>")
    return f"<![CDATA[{safe}]]>"


def _para(text: str) -> str:
    """Einfacher Text -> <p>-HTML (SNS rendert welcome/goodbye als HTML)."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    return "\n".join(f"<p>{ln}</p>" for ln in lines) + "\n"


def render_sns_xml(fragebogen: IsmFragebogen, name: str) -> str:
    """Rendert den Fragebogen als SNS-importierbares XML (A1, A4, A5).

    Struktur, Attribute und Faktorblock strukturgleich zur SNS-Referenz
    (BeispielOutput.xml). Items werden nach faktor_id sortiert ausgegeben
    (stabile Sortierung - Reihenfolge innerhalb eines Faktors bleibt wie
    geliefert/editiert).
    """
    parts: list[str] = []
    parts.append(
        "<questionnaire type='PROCESS' allowComments='true' randomize='true' "
        "defaultLanguage='de' languages='de' securityType='PUBLIC'>"
    )
    parts.append(f"<name>{_cdata(name)}</name>")
    parts.append(
        "<welcomeText><label lang='de'>"
        + _cdata(_para(fragebogen.begruessung))
        + "</label></welcomeText>"
    )
    parts.append(
        "<goodbyeText><label lang='de'>"
        + _cdata(_para(fragebogen.verabschiedung))
        + "</label></goodbyeText>"
    )
    parts.append(
        "<instruction><label lang='de'>"
        + _cdata(ISM_XML_INSTRUCTION)
        + "</label></instruction>"
    )
    parts.append(
        "<description><label lang='de'>"
        + _cdata("individualisiert")
        + "</label></description>"
    )

    # Faktorblock (A2: statisch)
    parts.append("<factors>")
    for f in ISM_FAKTOREN:
        parts.append(
            f"<factor id='{f['id']}' color='{ISM_XML_FACTOR_COLOR}' "
            "operation='SUM'>"
            f"<name><label lang='de'>{_cdata(f['name'])}</label></name>"
            "<description><label lang='de'>"
            + _cdata(f["beschreibung_xml"])
            + "</label></description></factor>"
        )
    parts.append("</factors>")

    # Items (A4/A5: feste Attribute, nur Texte variieren)
    parts.append("<questions>")
    for item in sorted(fragebogen.items, key=lambda it: it.faktor_id):
        parts.append(
            "<question type='SLIDER' allowComments='false' "
            "positionLocked='false' weights='1.0' changePoles='false' "
            f"factors='{item.faktor_id}'>"
            f"<title><label lang='de'>{_cdata(item.frage)}</label></title>"
            f"<min value='0'><label lang='de'>{_cdata(item.pol_min)}</label></min>"
            f"<max value='100'><label lang='de'>{_cdata(item.pol_max)}</label></max>"
            "</question>"
        )
    parts.append("</questions>")
    parts.append("<categories></categories>")
    parts.append("</questionnaire>")
    return "".join(parts)
