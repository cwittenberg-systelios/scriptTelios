"""v19.28 (S4/D7): QC fuer den thematischen Entlassbericht + Testwerte-Vollstaendigkeit."""
from app.services import quality_check as QC
from app.services.quality_check import (
    ISSUE_CODE_REDUNDANZ_ABSAETZE, ISSUE_CODE_TESTWERTE_FEHLEN,
    ISSUE_CODE_TESTWERTE_UNGUENSTIG_VERSCHWIEGEN, ISSUE_CODE_TESTWERTE_UNVOLLSTAENDIG,
    ISSUE_CODE_THEMA_KOHAERENZ, ISSUE_CODE_THEMA_NICHT_AUFGEGRIFFEN, QCContext,
    parse_testwert_paare, run_checks,
)

FF = """### Auftrag
A (Aufnahme 17.12.)

### Themenkandidaten
1. **Angst als alter Schutz** – Angst als Schutz- und Beziehungsregulation. Belege: (Einzel 23.12.)
2. **Türsteher und Faulheits-Selbstbild** – Belege: (Einzel 15.12.)

### Wendepunkte je Modalität
- Einzeltherapie: Elternbesuch als Belastungsprobe (Einzel 13.01., 20.01.)
- Gruppentherapie: keine Wendepunkte dokumentiert
- Nonverbale Therapien: Fatman-Zeichnung in der Kunsttherapie (08.01.)

### Symptomveränderung
s

### Offene Themen
o
"""

TEXT_GUT = """Zu Beginn formulierte Frau M. als Anliegen, wieder Halt zu finden.

Im Verlauf kristallisierte sich als zentrales Muster die Angst als alter Schutz heraus, verstrickt mit dem Türsteher und einem Faulheits-Selbstbild.

Im Einzelprozess wurde der Elternbesuch zur Belastungsprobe, in der die Angst als Schutz neu verstanden werden konnte.

In der Kunsttherapie entstand die Fatman-Zeichnung, mit der der Türsteher humorvoll betrachtet wurde.

Zum Abschluss ihres Prozesses reflektierte Frau M., dass die Angst leiser geworden sei und der Türsteher ihr weniger im Weg stehe; die Symptomatik hat sich im Vergleich zur Aufnahme deutlich gebessert.

Für den weiteren Verlauf empfehlen wir eine ambulante Vertiefung des Umgangs mit der Angst als Schutz."""

VORLAGE = (
    "Das ISR (ICD-10-Symptomrating) ... Werte (prä; post): Depression: 2.5; 0.5 (<1: unauffällig) "
    "Angst: 1.5; 1.5 (<1: unauffällig) Zwang: 0; 0 (<1) Somatisierung: 0; 0.67 (<0,75) "
    "Essstörung: 0; 0 (<0,67) Gesamtskala: 0.69; 0.48 (<0,6) "
    "Im DASS-21 (Depressions-Angst-Stress-Inventar) wurden nachfolgende Werte erreicht (prä; post): "
    "Depression: 12; 2 (<10) Angst: 2; 12 (<8) Stress: 16; 20 (<15) Körperlicher Untersuchungsbefund"
)


def _run(text, *, struktur="thematisch", ff=FF, vorlage=None, only):
    ctx = QCContext(text=text, workflow="entlassbericht", eb_struktur=struktur,
                    fallformel_text=ff, antragsvorlage_text=vorlage)
    return run_checks(ctx, only=only)


class TestSektionenJeStruktur:
    def test_thematisch_erwartet_thema_und_reflexion(self):
        codes = {i.code for i in _run("Nur ein Satz ohne Struktur.", only={"required_sections"})}
        assert "MISSING_SECTION_ZENTRALES_THEMA" in codes
        assert "MISSING_SECTION_REFLEXION_UND_SYMPTOMVERAENDERUNG" in codes
        assert "MISSING_SECTION_GESAMTBEWERTUNG" not in codes

    def test_statusquo_unveraendert(self):
        codes = {i.code for i in _run("Nur ein Satz ohne Struktur.", struktur="modalitaet", only={"required_sections"})}
        assert "MISSING_SECTION_GESAMTBEWERTUNG" in codes
        assert "MISSING_SECTION_ZENTRALES_THEMA" not in codes

    def test_guter_thematischer_text_besteht(self):
        assert _run(TEXT_GUT, only={"required_sections"}) == []


class TestThemaKohaerenz:
    def test_gut(self):
        assert _run(TEXT_GUT, only={"thema_kohaerenz", "wendepunkte"}) == []

    def test_thema_fehlt(self):
        txt = TEXT_GUT.replace("Türsteher", "Wächter").replace("Faulheits-Selbstbild", "Selbstbild")
        iss = _run(txt, only={"thema_kohaerenz"})
        assert [i.code for i in iss] == [ISSUE_CODE_THEMA_NICHT_AUFGEGRIFFEN]
        assert "Türsteher" in iss[0].message

    def test_thema_nur_einmal(self):
        txt = TEXT_GUT.replace("die Angst als Schutz neu", "das Erleben neu").replace(
            "dass die Angst leiser", "dass es leiser").replace("Umgangs mit der Angst als Schutz", "Umgangs mit sich")
        iss = _run(txt, only={"thema_kohaerenz"})
        assert any(i.code == ISSUE_CODE_THEMA_KOHAERENZ and "Angst als alter Schutz" in i.message for i in iss)

    def test_statusquo_still(self):
        assert _run("x", struktur="modalitaet", only={"thema_kohaerenz", "wendepunkte"}) == []

    def test_ohne_fallformel_still(self):
        assert _run("x", ff=None, only={"thema_kohaerenz", "wendepunkte"}) == []


class TestWendepunkte:
    def test_elternbesuch_fehlt(self):
        txt = TEXT_GUT.replace("Elternbesuch zur Belastungsprobe", "Besuch zur Probe")
        iss = _run(txt, only={"wendepunkte"})
        assert [i.code for i in iss] == ["WENDEPUNKT_NICHT_AUFGEGRIFFEN_EINZELTHERAPIE"]
        assert iss[0].severity == "info"

    def test_keine_wendepunkte_zeile_ignoriert(self):
        # Gruppentherapie: "keine Wendepunkte dokumentiert" -> nie ein Issue
        assert not any("GRUPPEN" in i.code for i in _run("x", only={"wendepunkte"}))


class TestRedundanz:
    def test_wiederholung_erkannt(self):
        s = "Die Angst diente als alter Schutz vor Überflutung und Kontrollverlust, biographisch verankert im Elternhaus."
        txt = f"{s}\n\n{s} Weiterer Satz.\n\n{s} Noch einer.\n\nEmpfehlungen folgen hier als Vertiefung."
        iss = _run(txt, only={"redundanz"})
        assert [i.code for i in iss] == [ISSUE_CODE_REDUNDANZ_ABSAETZE]
        assert iss[0].code_detail["pairs"][0]["jaccard"] >= 0.6

    def test_guter_text_ohne_redundanz(self):
        assert _run(TEXT_GUT, only={"redundanz"}) == []

    def test_nur_entlassbericht(self):
        s = "Die Angst diente als alter Schutz vor Überflutung und Kontrollverlust, biographisch verankert im Elternhaus."
        ctx = QCContext(text=f"{s}\n\n{s}\n\n{s}", workflow="verlaengerung")
        assert run_checks(ctx, only={"redundanz"}) == []


class TestTestwerteD7:
    def test_parse_vorlage(self):
        pairs = parse_testwert_paare(VORLAGE)
        assert [(p["instrument"], p["skala"], p["prae"], p["post"]) for p in pairs] == [
            ("ISR", "Depression", 2.5, 0.5), ("ISR", "Angst", 1.5, 1.5), ("ISR", "Zwang", 0, 0),
            ("ISR", "Somatisierung", 0, 0.67), ("ISR", "Essstörung", 0, 0), ("ISR", "Gesamtskala", 0.69, 0.48),
            ("DASS-21", "Depression", 12, 2), ("DASS-21", "Angst", 2, 12), ("DASS-21", "Stress", 16, 20),
        ]

    def test_s0_befund_dass_angst_verschwiegen(self):
        # Wortlaut des S0-Laufs EB-FrauM thematisch (2026-09-22)
        txt = ("In Bezug auf die Testwerte zeigte sich eine signifikante Verbesserung der depressiven Symptomatik "
               "(ISR Depression 2.5 → 0.5) sowie eine Reduktion der Depressionswerte im DASS-21 (12 → 2), während "
               "das Stresserleben im Zuge des Transferprozesses leicht anstieg (DASS-21 Stress 16 → 20).")
        iss = _run(txt, vorlage=VORLAGE, only={"testwerte"})
        codes = [i.code for i in iss]
        assert ISSUE_CODE_TESTWERTE_UNGUENSTIG_VERSCHWIEGEN in codes
        warn = next(i for i in iss if i.code == ISSUE_CODE_TESTWERTE_UNGUENSTIG_VERSCHWIEGEN)
        assert "DASS-21 Angst 2 → 12" in warn.message
        assert "ISR Somatisierung 0 → 0.67" in warn.message
        assert ISSUE_CODE_TESTWERTE_UNVOLLSTAENDIG in codes
        info = next(i for i in iss if i.code == ISSUE_CODE_TESTWERTE_UNVOLLSTAENDIG)
        assert "ISR Angst 1.5 → 1.5" in info.message and "Gesamtskala" in info.message
        # Zwang/Essstörung 0;0 nie gefordert
        assert "Zwang" not in info.message and "Essstörung" not in info.message

    def test_vollstaendig_kein_issue(self):
        txt = ("ISR: Depression 2,5 → 0,5, Angst 1,5 → 1,5, Somatisierung 0 → 0,67, Gesamtskala 0,69 → 0,48; "
               "DASS-21: Depression 12 → 2, Angst 2 → 12, Stress 16 → 20.")
        assert _run(txt, vorlage=VORLAGE, only={"testwerte"}) == []

    def test_gar_keine_testwerte_genannt(self):
        iss = _run("Bericht ohne Zahlen.", vorlage=VORLAGE, only={"testwerte"})
        assert [i.code for i in iss] == [ISSUE_CODE_TESTWERTE_FEHLEN]

    def test_ohne_vorlage_still(self):
        assert _run("x", vorlage=None, only={"testwerte"}) == []
        assert _run("x", vorlage="Anamnese ohne Zahlen", only={"testwerte"}) == []

    def test_nur_entlassbericht(self):
        ctx = QCContext(text="x", workflow="verlaengerung", antragsvorlage_text=VORLAGE)
        assert run_checks(ctx, only={"testwerte"}) == []

    def test_zahl_nicht_teil_groesserer_zahl(self):
        # "12 → 2" darf nicht durch "112 → 2" oder "12 → 20" befriedigt werden
        assert not QC._pair_in_text("Wert 112 → 2", "12", "2")
        assert not QC._pair_in_text("Wert 12 → 20", "12", "2")
        assert QC._pair_in_text("von 12 auf 2 Punkte", "12", "2")
