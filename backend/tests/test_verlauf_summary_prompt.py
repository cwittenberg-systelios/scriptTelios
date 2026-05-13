"""
Unit-Tests fuer die Prompt-Konstanten der Stage-1-Pipeline (v19.2 Schritt 1).
"""
from app.services.verlauf_summary import (
    VERLAUF_SUMMARY_SYSTEM_PROMPT,
    VERLAUF_SUMMARY_STRUCTURE,
    _build_focus_hint,
)


class TestSystemPrompt:
    """Stage-1-System-Prompt — die kritischen Anti-Halluzinations-Regeln."""

    def test_enthaelt_quellentreue_regel(self):
        assert "QUELLENTREUE" in VERLAUF_SUMMARY_SYSTEM_PROMPT
        assert "AUSSCHLIESSLICH" in VERLAUF_SUMMARY_SYSTEM_PROMPT

    def test_enthaelt_keine_interpretation(self):
        assert "KEINE INTERPRETATION" in VERLAUF_SUMMARY_SYSTEM_PROMPT

    def test_enthaelt_keine_wertung(self):
        assert "KEINE WERTUNG" in VERLAUF_SUMMARY_SYSTEM_PROMPT

    def test_enthaelt_verfahrensregel(self):
        # Verfahren nur uebernehmen wenn namentlich in der Quelle
        assert "IFS" in VERLAUF_SUMMARY_SYSTEM_PROMPT
        assert "namentlich" in VERLAUF_SUMMARY_SYSTEM_PROMPT

    def test_enthaelt_unsicherheits_kennzeichnung(self):
        assert "UNSICHERHEIT" in VERLAUF_SUMMARY_SYSTEM_PROMPT
        assert "unklar" in VERLAUF_SUMMARY_SYSTEM_PROMPT.lower()


class TestStructurePrompt:
    """Stage-1-Struktur-Prompt — die vier vorgeschriebenen Abschnitte."""

    def test_hat_vier_sections(self):
        # Die vier Sektionen muessen alle namentlich vorkommen
        assert "Sitzungsübersicht" in VERLAUF_SUMMARY_STRUCTURE
        assert "Bearbeitete Themen" in VERLAUF_SUMMARY_STRUCTURE
        assert "Therapeutische Interventionen" in VERLAUF_SUMMARY_STRUCTURE
        assert "Beobachtete Entwicklung" in VERLAUF_SUMMARY_STRUCTURE

    def test_vorgibt_section_marker(self):
        # ### als Section-Marker (analog zu clean_verlauf_text Datums-Trenner)
        assert "### Sitzungsübersicht" in VERLAUF_SUMMARY_STRUCTURE
        assert "### Bearbeitete Themen" in VERLAUF_SUMMARY_STRUCTURE


class TestFocusHintPerWorkflow:
    """Workflow-spezifische Fokus-Hinweise."""

    def test_verlaengerung_hint(self):
        hint = _build_focus_hint("verlaengerung")
        assert hint
        assert "Verlängerungsantrag" in hint

    def test_folgeverlaengerung_hint(self):
        hint = _build_focus_hint("folgeverlaengerung")
        assert hint
        assert "Folgeverlängerung" in hint
        # Charakteristisch: Veraenderung SEIT dem letzten Antrag
        assert "letzten" in hint.lower()

    def test_entlassbericht_hint(self):
        hint = _build_focus_hint("entlassbericht")
        assert hint
        assert "Entlassbericht" in hint
        # Charakteristisch: Gesamtbogen Anfang-Wendepunkte-Ende
        assert "Wendepunkte" in hint

    def test_anderer_workflow_kein_hint(self):
        assert _build_focus_hint("akutantrag") == ""
        assert _build_focus_hint("dokumentation") == ""
        assert _build_focus_hint(None) == ""
        assert _build_focus_hint("") == ""

    def test_hints_sind_unterschiedlich(self):
        # Die drei unterstuetzten Workflows muessen verschiedene Hints haben
        v = _build_focus_hint("verlaengerung")
        f = _build_focus_hint("folgeverlaengerung")
        e = _build_focus_hint("entlassbericht")
        assert v != f
        assert v != e
        assert f != e
