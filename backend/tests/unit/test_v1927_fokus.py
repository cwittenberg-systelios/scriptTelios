"""v19.27: Fokus-Treue - Stichpunkt-Terme/Abdeckung (quality_specs), QC-Regeln
stichpunkte + verfahren (quality_check_doku), Regressionsfall Job 89276fb6.

Entscheidungen: D6=A (Quote >= 50 % + Akronym-Pflicht, kein Komma-Split),
D7=A (STICHPUNKTE_IGNORIERT critical ab >= 2 Punkten, >= 50 % fehlend),
D13=A (VERFAHREN_PHASE_FEHLT info).
"""
import pytest

from app.services import quality_check as QC
from app.services.quality_specs import (
    split_stichpunkte,
    stichpunkt_akronyme,
    stichpunkt_coverage,
    stichpunkt_present,
    stichpunkt_terms,
)
from app.services.verfahren import erkannte_verfahren

# Realer Output des Ausloeserfalls (Job 89276fb6, 22.09.2026, gekuerzt auf
# die relevanten Saetze; Kuerzel Frau L.).
DOKU_89276 = """Auftragsklärung

Im Mittelpunkt stand zunächst das Erleben von Frau L. innerhalb einer Gruppensitzung zum Thema Perfektionismus. Frau L. schilderte, wie sie dort in eine defensive Haltung verfiel und sich zurückzog. Gemeinsames Ziel des Gesprächs war es, diese Reaktionsmuster zu reflektieren und die tieferliegenden traumatischen Erfahrungen zu bearbeiten.

Relevante Gesprächsinhalte

Frau L. berichtete von zwei wiederkehrenden belastenden Szenarien aus ihren Träumen und Erinnerungen. Während der Revisualisierung dieser Operation im Anschluss an die Geburt ihrer Tochter wurden detaillierte Wahrnehmungen deutlich: das Spüren des harten OP-Tisches, die Fixierung der Gliedmaßen sowie die Überwältigung durch Geräusche. Ein Versuch, die Ärzte aktiv aufzuhalten, scheiterte in der Vorstellung, woraufhin Frau L. bei ihrer verletzlichen Version ihres Ichs blieb, um ihr Trost und Nähe zu spenden.

Hypothesen und Entwicklungsperspektiven

Die defensive Haltung und der Rückzug in der Gruppe lassen sich als Schutzreaktion verstehen. Die Fähigkeit von Frau L., der verletzlichen Seite ihres Selbst beizustehen, stellt eine wesentliche Ressourcenaktivierung dar.

Einladungen

Frau L. wurde eingeladen, in den kommenden Tagen bewusst weniger zu sprechen und sich Zeit zu geben. Zudem wurde vereinbart, dass sie sich bei auftretenden schwierigen Erfahrungen an das Team wendet.
"""
BULLET_89276 = "IRRT Traumasitzung zur Operation im Anschluss an die Geburt der Tochter von Frau L."


# ── quality_specs ────────────────────────────────────────────────────────────

class TestTerme:
    def test_akronym_ist_pflicht_term(self):
        assert stichpunkt_akronyme(BULLET_89276) == ["irrt"]
        assert stichpunkt_terms(BULLET_89276)[0] == "irrt"

    def test_akronym_mit_ziffer_und_fuellwort(self):
        # "Sitzung" ist Fuellwort, "3" zu kurz -> bisher Fallback auf "sitzung"
        assert stichpunkt_terms("EMDR-Sitzung 3") == ["emdr"]

    def test_reines_fuellwort_ist_unpruefbar(self):
        assert stichpunkt_terms("Thema Arbeit") == []
        assert stichpunkt_present("irgendein Text", "Thema Arbeit") is True

    def test_kurzer_distinktiver_begriff_exakt(self):
        assert stichpunkt_terms("Wut") == ["wut"]
        assert stichpunkt_present("Sie beschreibt Wut auf den Vater.", "Wut") is True
        assert stichpunkt_present("Die Wutzelmaus.", "Wut") is False

    def test_split_ohne_komma(self):
        assert split_stichpunkte("- A und B, insb. C\n2) D; E") == ["A und B, insb. C", "D", "E"]


class TestCoverage:
    def test_regressionsfall_akronym_fehlt(self):
        cov = stichpunkt_coverage(DOKU_89276, BULLET_89276)
        assert cov["akronym_fehlt"] is True
        assert "irrt" in cov["fehlend"]
        assert stichpunkt_present(DOKU_89276, BULLET_89276) is False

    def test_mehrwort_nur_teilweise_abgedeckt(self):
        bullet = "Familien- und Paardynamik, Konflikt mit der Schwester, Abgrenzung"
        text = "Die Familiendynamik wurde besprochen."
        # 1 von 4 Termen (familien, paardynamik, konflikt, schwester, abgrenzung -> 5)
        assert stichpunkt_present(text, bullet) is False
        text2 = "Familiendynamik und Paardynamik, der Konflikt mit der Schwester."
        assert stichpunkt_present(text2, bullet) is True

    def test_zwei_terme_beide_noetig(self):
        assert stichpunkt_present("Schlafprobleme bestehen.", "Schlafprobleme anhaltend") is False
        assert stichpunkt_present("Anhaltende Schlafprobleme.", "Schlafprobleme anhaltend") is True

    def test_akronym_vorhanden_reicht_bei_einem_term(self):
        assert stichpunkt_present("Es wurde mit IFS gearbeitet.", "IFS") is True


# ── QC-Regeln ────────────────────────────────────────────────────────────────

def _codes(text, **kw):
    return [i.code for i in QC.run_quality_check(text, "dokumentation", **kw)]


class TestStichpunkteQC:
    def test_regressionsfall_89276fb6(self):
        issues = QC.run_quality_check(
            DOKU_89276, "dokumentation", source_text=DOKU_89276 + "\n" + BULLET_89276,
            stichpunkte=split_stichpunkte(BULLET_89276), verfahren_keys=["irrt"],
        )
        by_code = {i.code: i for i in issues}
        assert QC.ISSUE_CODE_MISSING_STICHPUNKT in by_code
        assert by_code[QC.ISSUE_CODE_MISSING_STICHPUNKT].code_detail["fehlend"][0] == "irrt"
        assert by_code[QC.ISSUE_CODE_MISSING_STICHPUNKT].code_detail["akronym_fehlt"] is True
        assert QC.ISSUE_CODE_VERFAHREN_NICHT_BENANNT in by_code
        assert by_code[QC.ISSUE_CODE_VERFAHREN_NICHT_BENANNT].severity == QC.SEVERITY_WARNING
        # ein einzelner Punkt -> kein Block-Issue (D7: ab 2 Punkten)
        assert QC.ISSUE_CODE_STICHPUNKTE_IGNORIERT not in by_code
        # korrekt uebernommene Begriffe aus dem Stichpunkt sind KEINE Erfindung
        assert QC.ISSUE_CODE_SOURCE_FIDELITY not in by_code

    def test_block_ignoriert_critical(self):
        text = DOKU_89276
        sp = ["IRRT Traumasitzung", "Schematherapie Modusarbeit", "Rückzug in der Gruppe"]
        issues = QC.run_quality_check(text, "dokumentation", stichpunkte=sp)
        crit = [i for i in issues if i.code == QC.ISSUE_CODE_STICHPUNKTE_IGNORIERT]
        assert len(crit) == 1 and crit[0].severity == QC.SEVERITY_CRITICAL
        assert crit[0].code_detail["total"] == 3
        assert len(crit[0].code_detail["fehlend"]) == 2

    def test_kein_block_issue_wenn_mehrheit_da(self):
        sp = ["Rückzug in der Gruppe", "Perfektionismus", "Trost und Nähe", "IRRT"]
        codes = _codes(DOKU_89276, stichpunkte=sp)
        assert codes.count(QC.ISSUE_CODE_MISSING_STICHPUNKT) == 1
        assert QC.ISSUE_CODE_STICHPUNKTE_IGNORIERT not in codes

    def test_stage1_kontext_in_message(self):
        issues = QC.run_quality_check(
            DOKU_89276, "dokumentation", stichpunkte=["IRRT"],
            stage1_audits={"transkript": {"applied": True}},
        )
        m = [i for i in issues if i.code == QC.ISSUE_CODE_MISSING_STICHPUNKT][0]
        assert "verdichtet" in m.message

    def test_ohne_stichpunkte_still(self):
        assert QC.ISSUE_CODE_MISSING_STICHPUNKT not in _codes(DOKU_89276)


class TestVerfahrenQC:
    def test_erkennung_aus_stichpunkten(self):
        assert [v.key for v in erkannte_verfahren("Transkript ohne Verfahren\n" + BULLET_89276)] == ["irrt"]
        assert erkannte_verfahren("") == []

    def test_benannt_keine_warnung_aber_phasen_info(self):
        text = DOKU_89276.replace("Während der Revisualisierung", "Während der IRRT-Sitzung")
        issues = QC.run_quality_check(text, "dokumentation", verfahren_keys=["irrt"])
        codes = [i.code for i in issues]
        assert QC.ISSUE_CODE_VERFAHREN_NICHT_BENANNT not in codes
        phase = [i for i in issues if i.code == QC.ISSUE_CODE_VERFAHREN_PHASE_FEHLT]
        assert phase and phase[0].severity == QC.SEVERITY_INFO
        assert "gelungen" not in phase[0].message  # keine Bewertung

    def test_phasen_check_nur_p1(self):
        text = "Im Verlauf kam IRRT zum Einsatz. " * 30
        issues = QC.run_quality_check(text, "verlaengerung", verfahren_keys=["irrt"])
        assert QC.ISSUE_CODE_VERFAHREN_PHASE_FEHLT not in [i.code for i in issues]

    def test_unbekannter_key_ignoriert(self):
        assert QC.ISSUE_CODE_VERFAHREN_NICHT_BENANNT not in _codes(DOKU_89276, verfahren_keys=["gibtsnicht"])

    def test_wortgrenze_ifs(self):
        from app.services.verfahren import VERFAHREN_BY_KEY, verfahren_benannt
        assert verfahren_benannt("Beifall im Raum", VERFAHREN_BY_KEY["ifs"]) is False
        assert verfahren_benannt("mit IFS gearbeitet", VERFAHREN_BY_KEY["ifs"]) is True


@pytest.mark.parametrize("wf", ["anamnese", "verlaengerung", "entlassbericht"])
def test_registry_kennt_die_regeln(wf):
    names = {c.name: c for c in QC.CHECK_REGISTRY}
    assert set(names["stichpunkte"].codes) == {
        QC.ISSUE_CODE_MISSING_STICHPUNKT, QC.ISSUE_CODE_STICHPUNKTE_IGNORIERT,
    }
    assert set(names["verfahren"].codes) == {
        QC.ISSUE_CODE_VERFAHREN_NICHT_BENANNT, QC.ISSUE_CODE_VERFAHREN_PHASE_FEHLT,
    }
    # Serialisierung traegt checks_run (D8 Status-Meldung)
    ser = QC.serialize_issues([], workflow=wf)
    assert ser["summary"]["checks_run"] == len(QC.CHECK_REGISTRY)
    assert ser["version"] == 1
