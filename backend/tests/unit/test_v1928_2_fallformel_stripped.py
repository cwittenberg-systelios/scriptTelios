"""v19.28.2: Fallformel nach strip_markdown_formatting (Feedback 24.09.2026,
Jobs a4777926 / d3d33845): Ueberschriften ohne '### ', Titel ohne Fettdruck."""
from app.services.fallformel import (
    detect_fallformel_issues, normalize_fallformel, parse_themenkandidaten, select_themen, split_sections,
)
from app.services.llm import strip_markdown_formatting

# Wortlaut aus prompts.log (Job a4777926, gekuerzt)
STRIPPED = """Auftrag
Herr B. sucht Unterstützung nach wiederholten beruflichen Zusammenbrüchen (Antragsvorlage).

Themenkandidaten
1. Kompetenzfassade durch Leistungsdruck – Ein Muster aus strengem Fordern und kompensatorischem Perfektionismus dient als Schutzreaktion (Einzel 31.07., Einzel 06.08.). Belege: (Einzel 31.07.), (Einzel 06.08.)
2. Körperliche Signale als Bedürfnisrückmeldung – Die Wahrnehmung körperlicher Symptome verschiebt sich (Einzel 29.07.). Belege: (Einzel 29.07.)
3. Aufmerksamkeit über Krisen generieren – Die Tendenz, in Krisensituationen Aufmerksamkeit zu suchen (Einzel 24.08.). Beleg: (Einzel 24.08.)

Wendepunkte je Modalität
- Einzeltherapie: Erkenntnis der Differenzierung zwischen dem gesamten Selbst und einzelnen Anteilen.
- Gruppentherapie: Übergang von einem anfangs verschlossenen Zustand hin zu einer Öffnung (Gruppe 03.08.).
- Nonverbale Therapien: Transformation eines Ausgangsbildes in eine freundliche Blumenwiese (Kunsttherapie 06.08.).

Symptomveränderung
Ausgangszustand war geprägt von emotionaler Überforderung (Einzel 28.07.).

Offene Themen
Die Übertragung der erreichten Erfolge in den beruflichen Kontext bleibt offen (Epikrise)."""

MD = """### Auftrag
A

### Themenkandidaten
1. **T1** – x Belege: (Einzel 31.07.)

### Wendepunkte je Modalität
- Einzeltherapie: e

### Symptomveränderung
s

### Offene Themen
o"""


def test_gestrippte_ueberschriften_werden_erkannt():
    sec = split_sections(STRIPPED)
    assert list(sec) == ["Auftrag", "Themenkandidaten", "Wendepunkte je Modalität", "Symptomveränderung", "Offene Themen"]
    assert sec["Offene Themen"].startswith("Die Übertragung")


def test_themen_aus_gestripptem_output():
    items = parse_themenkandidaten(STRIPPED)
    assert len(items) == 3
    assert items[0].startswith("Kompetenzfassade durch Leistungsdruck")


def test_normalize_stellt_sollformat_her():
    out = normalize_fallformel(STRIPPED)
    assert out.startswith("### Auftrag\n")
    assert "\n\n### Themenkandidaten\n1. Kompetenzfassade" in out
    assert out.count("### ") == 5
    # idempotent + Markdown-Eingabe unveraendert
    assert normalize_fallformel(out) == out
    assert normalize_fallformel(MD) == MD


def test_keine_issues_mehr_fuer_gestrippten_output():
    src = "31.07. 06.08. 29.07. 24.08. 03.08. 28.07. Sitzungen"
    issues = detect_fallformel_issues(normalize_fallformel(STRIPPED), src)
    assert not [i for i in issues if i["type"] == "abschnitt_fehlt"]


def test_roundtrip_durch_postprocessing():
    """Was strip_markdown_formatting aus dem Sollformat macht, muss zurueck ins Sollformat."""
    stripped = strip_markdown_formatting(MD)
    assert "###" not in stripped and "**" not in stripped
    norm = normalize_fallformel(stripped)
    assert split_sections(norm) == split_sections(MD.replace("**T1**", "T1"))
    assert parse_themenkandidaten(select_themen(norm, None)) == ["T1 – x Belege: (Einzel 31.07.)"]


def test_ueberschrift_mit_doppelpunkt_und_fett():
    txt = "**Auftrag:**\nA\n\nThemenkandidaten:\n1. T – d\n\nWendepunkte je Modalität\n- Einzeltherapie: e\n\nSymptomveränderung\ns\n\nOffene Themen\no"
    assert list(split_sections(txt)) == ["Auftrag", "Themenkandidaten", "Wendepunkte je Modalität", "Symptomveränderung", "Offene Themen"]


def test_flesstext_mit_abschnittswort_im_satz_ist_keine_ueberschrift():
    txt = "### Auftrag\nDer Auftrag war klar.\nOffene Themen gab es viele.\n\n### Offene Themen\no"
    assert list(split_sections(txt)) == ["Auftrag", "Offene Themen"]
    assert "Offene Themen gab es viele." in split_sections(txt)["Auftrag"]


def test_gruppentherapeutisch_zaehlt_als_gruppentherapie():
    from app.services.quality_specs import section_present
    assert section_present("Die gruppentherapeutischen Angebote nutzte er zunehmend.", "Gruppentherapie")
