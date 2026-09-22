"""v19.27: Stage-1-Audit als QC-Issues (D1=A, D2=B) und P1-Strukturregeln
(D3=A Laenge < 150, D4=A Organisatorisches-Platzhalter, D5=B Listenformat +
duenne Abschnitte, Einladungen-Fallback/generisch)."""
import pytest

from app.services import quality_check as QC
from app.services.quality_check_doku import doku_sections


def _mk(auftrag="", inhalte="", hyp="", einl="", orga=None):
    t = (
        f"Auftragsklärung\n\n{auftrag}\n\nRelevante Gesprächsinhalte\n\n{inhalte}\n\n"
        f"Hypothesen und Entwicklungsperspektiven\n\n{hyp}\n\nEinladungen\n\n{einl}\n"
    )
    if orga is not None:
        t += f"\nOrganisatorisches\n\n{orga}\n"
    return t


_LANG = ("Frau K. beschreibt, dass sie sich im Team zunehmend zurueckzieht und "
         "abends eine grosse Leere spuert. Sie verbindet das mit dem Anspruch, "
         "niemanden zu enttaeuschen. Im Gespraech wurde deutlich, dass dieser "
         "Anspruch frueh gelernt wurde. ")
_ANFANG = _LANG * 2
_MITTE = _LANG * 4
_HYP = _LANG * 2
_EINL = "Frau K. wurde eingeladen, vor jeder Zusage kurz innezuhalten."

VOLL = _mk(_ANFANG, _MITTE, _HYP, _EINL)


def _codes(text, wf="dokumentation", **kw):
    return [i.code for i in QC.run_quality_check(text, wf, **kw)]


class TestSections:
    def test_parser_findet_abschnitte(self):
        secs = doku_sections(_mk("A", "B", "C", "D", "E"))
        assert list(secs) == ["Auftragsklärung", "Relevante Gesprächsinhalte",
                              "Hypothesen und Entwicklungsperspektiven", "Einladungen", "Organisatorisches"]
        assert secs["Einladungen"] == "D" and secs["Organisatorisches"] == "E"

    def test_fette_ueberschrift(self):
        secs = doku_sections("**Auftragsklärung**\nText\n**Einladungen**\nX")
        assert secs["Auftragsklärung"] == "Text"


class TestStruktur:
    def test_voller_text_still(self):
        codes = _codes(VOLL)
        for c in (QC.ISSUE_CODE_ORGANISATORISCHES_PLATZHALTER, QC.ISSUE_CODE_EINLADUNG_GENERISCH,
                  QC.ISSUE_CODE_EINLADUNG_FALLBACK, QC.ISSUE_CODE_DOKU_LISTENFORMAT,
                  QC.ISSUE_CODE_ABSCHNITT_DUENN, "LENGTH_BELOW_TARGET"):
            assert c not in codes, c

    def test_orga_platzhalter(self):
        codes = _codes(_mk(_ANFANG, _MITTE, _HYP, _EINL, orga="Es wurden keine organisatorischen Punkte besprochen."))
        assert QC.ISSUE_CODE_ORGANISATORISCHES_PLATZHALTER in codes

    def test_orga_echt_still(self):
        orga = ("Ein Transfergespraech mit der ambulanten Therapeutin ist fuer die "
                "kommende Woche geplant; Frau K. klaert die Terminfindung selbst.")
        assert QC.ISSUE_CODE_ORGANISATORISCHES_PLATZHALTER not in _codes(_mk(_ANFANG, _MITTE, _HYP, _EINL, orga=orga))

    def test_einladung_fallback_info(self):
        issues = QC.run_quality_check(
            _mk(_ANFANG, _MITTE, _HYP, "Es wurde keine konkrete Einladung oder Aufgabe vereinbart."),
            "dokumentation")
        i = [x for x in issues if x.code == QC.ISSUE_CODE_EINLADUNG_FALLBACK]
        assert i and i[0].severity == QC.SEVERITY_INFO

    def test_einladung_generisch_nur_mit_quelle(self):
        text = _mk(_ANFANG, _MITTE, _HYP, "Frau K. wurde eingeladen, ein Tagebuch zu fuehren.")
        assert QC.ISSUE_CODE_EINLADUNG_GENERISCH not in _codes(text)
        assert QC.ISSUE_CODE_EINLADUNG_GENERISCH in _codes(text, source_text="Transkript ohne Aufgabe.")
        assert QC.ISSUE_CODE_EINLADUNG_GENERISCH not in _codes(
            text, source_text="Ich lade Sie ein, ein Tagebuch zu fuehren.")

    def test_listenformat(self):
        text = _mk(_ANFANG, "- Rueckzug im Team\n- Leere am Abend\n- Anspruch\n", _HYP, _EINL)
        assert QC.ISSUE_CODE_DOKU_LISTENFORMAT in _codes(text)

    def test_duenner_abschnitt_info(self):
        issues = QC.run_quality_check(_mk("Ein Satz.", _MITTE, _HYP, _EINL), "dokumentation")
        i = [x for x in issues if x.code == QC.ISSUE_CODE_ABSCHNITT_DUENN]
        assert i and i[0].severity == QC.SEVERITY_INFO
        assert i[0].code_detail["abschnitte"][0]["section"] == "Auftragsklärung"

    def test_einladungen_duerfen_kurz_sein(self):
        assert QC.ISSUE_CODE_ABSCHNITT_DUENN not in _codes(VOLL)

    @pytest.mark.parametrize("wf", ["anamnese", "verlaengerung", "entlassbericht"])
    def test_nur_dokumentation(self, wf):
        text = _mk("Ein Satz.", "- a\n- b\n", _HYP, _EINL, orga="entfaellt")
        codes = _codes(text, wf=wf)
        for c in (QC.ISSUE_CODE_ORGANISATORISCHES_PLATZHALTER, QC.ISSUE_CODE_DOKU_LISTENFORMAT,
                  QC.ISSUE_CODE_ABSCHNITT_DUENN):
            assert c not in codes


class TestLaenge:
    def test_unter_ziel_warning(self):
        text = _mk(_LANG, _LANG, _LANG, _EINL)  # ~120 Woerter
        n = len(text.split())
        assert 75 <= n < 150
        issues = QC.run_quality_check(text, "dokumentation")
        i = [x for x in issues if x.code == "LENGTH_BELOW_TARGET"]
        assert i and i[0].severity == QC.SEVERITY_WARNING and i[0].code_detail["actual"] == n
        assert QC.ISSUE_CODE_LENGTH_TOO_SHORT not in [x.code for x in issues]

    def test_stub_bleibt_generisch(self):
        codes = _codes("Auftragsklärung\n\nKurz.\n")
        assert QC.ISSUE_CODE_LENGTH_TOO_SHORT in codes and "LENGTH_BELOW_TARGET" not in codes

    def test_andere_workflows_unberuehrt(self):
        text = _mk(_LANG, _LANG, _LANG, _EINL)
        assert "LENGTH_BELOW_TARGET" not in _codes(text, wf="verlaengerung")


class TestStage1Audit:
    def _run(self, audits):
        return QC.run_quality_check(VOLL, "dokumentation", stage1_audits=audits)

    def test_sauber_still(self):
        codes = [i.code for i in self._run({"transkript": {"applied": True, "degraded": False, "issues": []}})]
        assert not any(c.startswith("VERDICHTUNG_") for c in codes)

    def test_skip_wegen_kuerze_still(self):
        codes = [i.code for i in self._run({"transkript": {"applied": False, "fallback_reason": "transkript_kurz_900w_min_2800w"}})]
        assert QC.ISSUE_CODE_STAGE1_FALLBACK not in codes

    def test_exception_info(self):
        issues = self._run({"transkript": {"applied": False, "fallback_reason": "exception: Stage1Error: leer"}})
        i = [x for x in issues if x.code == QC.ISSUE_CODE_STAGE1_FALLBACK]
        assert i and i[0].severity == QC.SEVERITY_INFO

    def test_degraded_warning(self):
        issues = self._run({"transkript": {"applied": True, "degraded": True, "summary_word_count": 500,
                                            "target_words": 1000, "retry_used": True}})
        i = [x for x in issues if x.code == QC.ISSUE_CODE_STAGE1_VERDICHTUNG_DEGRADED]
        assert i and i[0].severity == QC.SEVERITY_WARNING and i[0].code_detail["retry_used"] is True

    def test_halluzination_verfahren_warning_icd_critical(self):
        w = self._run({"verlauf": {"applied": True, "issues": [
            {"type": "verfahren_halluzination", "severity": "high", "detail": "Verfahren 'EMDR' in Zusammenfassung aber nicht in Quelle"}]}})
        c = self._run({"verlauf": {"applied": True, "issues": [
            {"type": "icd_halluzination", "severity": "critical", "detail": "F33.1"}]}})
        w = [x for x in w if x.code == QC.ISSUE_CODE_STAGE1_HALLUZINATION][0]
        c = [x for x in c if x.code == QC.ISSUE_CODE_STAGE1_HALLUZINATION][0]
        assert w.severity == QC.SEVERITY_WARNING and "Verlaufsdokumentation" in w.message
        assert c.severity == QC.SEVERITY_CRITICAL

    def test_stage1_short_ist_kein_qc_issue(self):
        issues = self._run({"transkript": {"applied": True, "issues": [{"type": "stage1_short"}]}})
        assert QC.ISSUE_CODE_STAGE1_HALLUZINATION not in [i.code for i in issues]

    def test_none_und_leer(self):
        assert not any(i.code.startswith("VERDICHTUNG_") for i in self._run(None))
        assert not any(i.code.startswith("VERDICHTUNG_") for i in self._run({"transkript": None, "verlauf": None}))
