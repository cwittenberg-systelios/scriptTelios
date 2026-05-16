"""
Tests fuer app/services/transcript_summary.py.

Schwerpunkt: _wir_hint workflow-Logik. Die eigentliche LLM-Verdichtung
ist via Mock erreichbar, aber der dort verwendete LLM-Mock wuerde nichts
Aussagekraeftiges produzieren — diese Aspekte testen wir lieber bei
verlauf_summary (selbe Halluzinations-Funktion).
"""
import pytest

from app.services.transcript_summary import (
    _wir_hint,
    WIR_WORKFLOWS,
)


# ─────────────────────────────────────────────────────────────────────────────
# _wir_hint: Wir-Form vs neutral-deskriptiv
# ─────────────────────────────────────────────────────────────────────────────


class TestWirHint:

    @pytest.mark.parametrize("wf", sorted(WIR_WORKFLOWS))
    def test_antrags_workflows_bekommen_wir_hint(self, wf):
        hint = _wir_hint(wf)
        assert "Wir" in hint or "wir" in hint.lower()

    @pytest.mark.parametrize("wf", ["dokumentation", "anamnese", "befund"])
    def test_andere_workflows_bekommen_neutralen_hint(self, wf):
        hint = _wir_hint(wf)
        assert "neutral" in hint.lower() or "berichtet" in hint.lower()
        assert "Wir nehmen" not in hint

    def test_none_workflow_neutral(self):
        hint = _wir_hint(None)
        assert "neutral" in hint.lower() or "berichtet" in hint.lower()

    def test_unbekannter_workflow_neutral(self):
        hint = _wir_hint("foo_bar_baz")
        assert "neutral" in hint.lower() or "berichtet" in hint.lower()
