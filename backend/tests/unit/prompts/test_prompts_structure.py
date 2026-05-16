"""
Strukturelle Workflows, Konstanten-Konsistenz.

Konsolidiert aus den frueheren Versions-Dateien test_prompts_v13.py bis
test_prompts_v16.py. Tests sind unveraendert, nur nach Feature-Area
umgruppiert. Versions-Geschichte steht im Git-Log.
"""
import pytest
import re
from unittest.mock import patch, MagicMock

# ──── aus test_prompts_v13.py: TestStructuralWorkflowsConstant
class TestStructuralWorkflowsConstant:
    """STRUCTURAL_WORKFLOWS muss konsistent in allen Code-Pfaden verwendet werden."""

    def test_konstante_existiert_und_enthaelt_korrekte_workflows(self):
        from app.services.prompts import STRUCTURAL_WORKFLOWS
        assert "anamnese" in STRUCTURAL_WORKFLOWS
        assert "verlaengerung" in STRUCTURAL_WORKFLOWS
        assert "folgeverlaengerung" in STRUCTURAL_WORKFLOWS
        assert "akutantrag" in STRUCTURAL_WORKFLOWS
        assert "entlassbericht" in STRUCTURAL_WORKFLOWS
        # P1 (dokumentation) ist NICHT strukturell
        assert "dokumentation" not in STRUCTURAL_WORKFLOWS

    def test_alle_strukturellen_workflows_haben_base_prompt(self):
        from app.services.prompts import STRUCTURAL_WORKFLOWS, BASE_PROMPTS
        for wf in STRUCTURAL_WORKFLOWS:
            # akutantrag hat einen separaten BASE_PROMPT_AKUTANTRAG, ist aber
            # nicht zwingend in BASE_PROMPTS - das ist OK.
            assert wf in BASE_PROMPTS or wf == "akutantrag"


# ── Bug 1b: PATIENTENNAME-Block im System-Prompt ─────────────────────────────

