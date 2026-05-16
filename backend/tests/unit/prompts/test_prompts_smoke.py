"""
Smoke-Tests: alle Workflows einmal durch build_system_prompt+build_user_content.

Konsolidiert aus den frueheren Versions-Dateien test_prompts_v13.py bis
test_prompts_v16.py. Tests sind unveraendert, nur nach Feature-Area
umgruppiert. Versions-Geschichte steht im Git-Log.
"""
import pytest
import re
from unittest.mock import patch, MagicMock

# ──── aus test_prompts_v14.py: TestSmokeAllWorkflows
class TestSmokeAllWorkflows:
    """Alle Workflows muessen weiterhin valide System-Prompts erzeugen."""

    @pytest.mark.parametrize("wf", [
        "dokumentation", "anamnese", "verlaengerung",
        "folgeverlaengerung", "akutantrag", "entlassbericht",
    ])
    def test_workflow_baut_validen_prompt(self, wf):
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow=wf,
            patient_name={"anrede": "Frau", "vorname": "Maria",
                          "nachname": "Schmidt", "initial": "S."},
            diagnosen=["F33.1 Rezidivierende depressive Störung"],
        )
        assert len(p) > 500
        assert "[Patient/in]" not in p or "Frau S." in p  # Replace funktionierte

    @pytest.mark.parametrize("wf", [
        "dokumentation", "anamnese", "verlaengerung",
        "folgeverlaengerung", "akutantrag", "entlassbericht",
    ])
    def test_workflow_baut_validen_user_content(self, wf):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow=wf,
            transcript="Patient berichtet von erschoepfender Arbeitsphase.",
            patient_name={"anrede": "Frau", "vorname": "Maria",
                          "nachname": "Schmidt", "initial": "S."},
        )
        assert len(u) > 50
        assert "die Klientin/der Klient" not in u

# ──── aus test_prompts_v15.py: TestSmokeAllWorkflows
class TestSmokeAllWorkflows:
    """Alle Workflows muessen weiter valide System-Prompts und User-Contents bauen."""

    @pytest.mark.parametrize("wf", [
        "dokumentation", "anamnese", "verlaengerung",
        "folgeverlaengerung", "akutantrag", "entlassbericht",
    ])
    def test_user_content_baut_ohne_klient_klient(self, wf):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow=wf,
            transcript="Test-Transkript.",
            patient_name={"anrede": "Frau", "vorname": "Maria",
                          "nachname": "Schmidt", "initial": "S."},
        )
        assert "die Klientin/der Klient" not in u
        # Aktueller Patient muss korrekt benannt sein
        assert "Frau S." in u

