"""
Datenschutz-Namensregel (Initialen-Konsolidierung, 1x pro User-Content).

Konsolidiert aus den frueheren Versions-Dateien test_prompts_v13.py bis
test_prompts_v16.py. Tests sind unveraendert, nur nach Feature-Area
umgruppiert. Versions-Geschichte steht im Git-Log.
"""
import pytest
import re
from unittest.mock import patch, MagicMock

# ──── aus test_prompts_v15.py: TestNamensregelKonsolidierung
class TestNamensregelKonsolidierung:
    """Die Datenschutz-Namensregel darf nicht mehr 5x pro User-Content erscheinen."""

    def test_namensregel_genau_einmal_in_user_content(self):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="entlassbericht",
            verlaufsdoku_text="Test-Verlauf.",
            antragsvorlage_text="Test-Antrag.",
            patient_name={"anrede": "Frau", "vorname": "Maria",
                          "nachname": "Schmidt", "initial": "S."},
        )
        # Die Header-Zeile darf genau einmal vorkommen
        count = u.count("DATENSCHUTZ – NAMENSFORMAT")
        assert count == 1, (
            f"NAMENSREGEL muesste 1x vorkommen, ist aber {count}x in:\n{u[:500]}"
        )

    @pytest.mark.parametrize("wf", [
        "dokumentation", "anamnese", "verlaengerung",
        "folgeverlaengerung", "akutantrag", "entlassbericht",
    ])
    def test_namensregel_in_jedem_workflow_genau_einmal(self, wf):
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow=wf,
            transcript="Test.",
            patient_name={"anrede": "Frau", "vorname": "M.",
                          "nachname": "Schmidt", "initial": "S."},
        )
        count = u.count("DATENSCHUTZ – NAMENSFORMAT")
        assert count == 1, f"Workflow {wf}: NAMENSREGEL {count}x statt 1x"

    def test_namensregel_konstante_existiert(self):
        from app.services.prompts import NAMENSREGEL
        assert "DATENSCHUTZ" in NAMENSREGEL
        assert "Initialen" in NAMENSREGEL or "ersten Buchstaben" in NAMENSREGEL


# ── Opt 4: Transcript-Deduplikation ───────────────────────────────────────────

