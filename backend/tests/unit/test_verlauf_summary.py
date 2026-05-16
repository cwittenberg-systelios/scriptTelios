"""
Tests fuer app/services/verlauf_summary.py.

Schwerpunkt: detect_summary_hallucination_signals (4 Severity-Stufen).
Die Funktion ist rein-funktional und perfekt unit-testbar. Vor der
Refactor-Welle hatte sie 0% Coverage.
"""
import pytest

from app.services.verlauf_summary import (
    detect_summary_hallucination_signals,
    _build_focus_hint,
    KNOWN_VERFAHREN,
)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Verfahrens-Halluzinationen (severity=high)
# ─────────────────────────────────────────────────────────────────────────────


class TestVerfahrenHalluzination:

    def test_summary_nennt_emdr_obwohl_nicht_in_quelle(self):
        source = "Patientin hatte heute eine Einzelsitzung. Themen: Bindung zur Mutter."
        summary = "Therapeut nutzte EMDR zur Stabilisierung."
        issues = detect_summary_hallucination_signals(summary, source)
        assert any(
            i["type"] == "verfahren_halluzination" and "EMDR" in i["detail"]
            for i in issues
        )
        assert any(i["severity"] == "high" for i in issues)

    def test_summary_nennt_ifs_obwohl_nicht_in_quelle(self):
        source = "Pat. spricht ueber Wut. Erstaunlich klar formuliert."
        summary = "Mit IFS-Modell wurde der Beschuetzer-Anteil identifiziert."
        issues = detect_summary_hallucination_signals(summary, source)
        assert any(i["type"] == "verfahren_halluzination" for i in issues)

    def test_verfahren_in_quelle_kein_alarm(self):
        source = "Es kam EMDR zum Einsatz, bilateral. Anschliessend stabilisiert."
        summary = "Therapeut nutzte EMDR-Verfahren zur Trauma-Verarbeitung."
        issues = detect_summary_hallucination_signals(summary, source)
        assert not any(i["type"] == "verfahren_halluzination" for i in issues)

    @pytest.mark.parametrize("verfahren", KNOWN_VERFAHREN)
    def test_alle_known_verfahren_werden_erkannt(self, verfahren):
        """Wenn ein KNOWN_VERFAHREN nur in Summary aber nicht in Quelle steht: Treffer."""
        source = "Sitzung verlief ruhig. Patient stabil."
        summary = f"Methode der Wahl war {verfahren}."
        issues = detect_summary_hallucination_signals(summary, source)
        assert any(i["type"] == "verfahren_halluzination" for i in issues)


# ─────────────────────────────────────────────────────────────────────────────
# 2. ICD-Halluzinationen (severity=critical)
# ─────────────────────────────────────────────────────────────────────────────


class TestICDHalluzination:

    def test_neue_icd_in_summary_critical(self):
        source = "Diagnose F32.1. Patientin stabil."
        summary = "Diagnosen: F32.1 und F43.2."  # F43.2 ist neu!
        issues = detect_summary_hallucination_signals(summary, source)
        assert any(
            i["type"] == "icd_halluzination"
            and i["severity"] == "critical"
            and "F43.2" in i["detail"]
            for i in issues
        )
        # F32.1 ist legit, soll NICHT gemeldet werden
        assert not any("F32.1" in i["detail"] for i in issues
                       if i["type"] == "icd_halluzination")

    def test_z_codes_werden_erkannt(self):
        source = "Anamnese ohne Auffaelligkeiten."
        summary = "Diagnose: Z73.0 Ausgebranntsein."
        issues = detect_summary_hallucination_signals(summary, source)
        assert any(
            i["type"] == "icd_halluzination" and "Z73.0" in i["detail"]
            for i in issues
        )

    def test_icd_in_beiden_kein_alarm(self):
        source = "Aufgenommen mit F33.1 und F45.41."
        summary = "Bei F33.1 wurde mit kognitiv-verhaltenstherapeutischen Methoden gearbeitet, F45.41 stand im Hintergrund."
        issues = detect_summary_hallucination_signals(summary, source)
        assert not any(i["type"] == "icd_halluzination" for i in issues)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Wortlaut-Halluzinationen (severity=medium)
# ─────────────────────────────────────────────────────────────────────────────


class TestWortlautHalluzination:

    def test_summary_zitiert_aber_quelle_nicht(self):
        source = "Patient war ruhig und hat reflektiert."
        summary = 'Patient sagte: "Ich will nicht mehr leben."'
        issues = detect_summary_hallucination_signals(summary, source)
        assert any(i["type"] == "wortlaut_halluzination" for i in issues)

    def test_summary_paraphrasiert_kein_alarm(self):
        source = "Patient war ruhig."
        summary = "Patientin schildert eine ruhige Stimmungslage."
        issues = detect_summary_hallucination_signals(summary, source)
        assert not any(i["type"] == "wortlaut_halluzination" for i in issues)

    def test_quelle_zitiert_summary_paraphrasiert_kein_alarm(self):
        # Quelle enthaelt Zitat-Wendung -> Summary darf auch zitieren
        source = 'Pat. sagte: "Mir geht es schlecht."'
        summary = 'Pat. aeusserte sich mit den Worten: "Mir geht es schlecht."'
        issues = detect_summary_hallucination_signals(summary, source)
        # kein wortlaut_halluzination weil Pattern auch in Quelle existiert
        # (Pattern: r'sagte[:,]?\s*"' wird in source gefunden -> kein Treffer)
        wortlaut_issues = [i for i in issues if i["type"] == "wortlaut_halluzination"]
        assert len(wortlaut_issues) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. Anzahl-Plausibilitaet (severity=medium)
# ─────────────────────────────────────────────────────────────────────────────


class TestAnzahlImplausibel:

    def test_zu_viele_sitzungen_bei_wenigen_datums_markern(self):
        # _DATE_RE braucht Jahr: "DD.MM.YYYY" oder "DD.MM.YY"
        source = "Sitzung am 01.01.2026: Aufnahme. Sitzung am 02.01.2026: stabil."
        summary = "Es fanden 20 Einzelgespraeche statt, alle erfolgreich."
        issues = detect_summary_hallucination_signals(summary, source)
        assert any(i["type"] == "anzahl_implausibel" for i in issues)

    def test_plausible_anzahl_kein_alarm(self):
        source = ("Sitzungen am 01.01.2026, 02.01.2026, 03.01.2026, "
                  "04.01.2026, 05.01.2026.")
        summary = "5 Einzelgespraeche fanden statt."
        issues = detect_summary_hallucination_signals(summary, source)
        assert not any(i["type"] == "anzahl_implausibel" for i in issues)

    def test_kleine_zahl_kein_alarm_trotz_keinen_daten(self):
        # n <= 5 wird nicht als implausibel markiert (Floor in der Logik)
        source = "Bericht ohne konkrete Daten."
        summary = "2 Einzelgespraeche fanden statt."
        issues = detect_summary_hallucination_signals(summary, source)
        assert not any(i["type"] == "anzahl_implausibel" for i in issues)


# ─────────────────────────────────────────────────────────────────────────────
# Edge-Cases
# ─────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:

    def test_leere_summary_keine_issues(self):
        assert detect_summary_hallucination_signals("", "Quelltext") == []

    def test_leere_quelle_keine_issues(self):
        assert detect_summary_hallucination_signals("Summary", "") == []

    def test_beide_leer_keine_issues(self):
        assert detect_summary_hallucination_signals("", "") == []

    def test_returns_list_immer(self):
        result = detect_summary_hallucination_signals("ok", "ok")
        assert isinstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# _build_focus_hint
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildFocusHint:

    @pytest.mark.parametrize("wf", [
        "verlaengerung", "folgeverlaengerung", "entlassbericht",
    ])
    def test_workflow_mit_fokus(self, wf):
        hint = _build_focus_hint(wf)
        assert hint
        assert len(hint) > 30  # mindestens ein Satz

    def test_unbekannter_workflow_leer(self):
        assert _build_focus_hint("foo") == ""
        assert _build_focus_hint("dokumentation") == ""  # nicht in Whitelist

    def test_none_workflow_leer(self):
        assert _build_focus_hint(None) == ""

    def test_folgeverlaengerung_erwaehnt_seit_letztem(self):
        """Sanity-Check dass die Hints workflow-spezifisch sind."""
        hint = _build_focus_hint("folgeverlaengerung")
        assert "letzten" in hint.lower() or "seit" in hint.lower()
