"""
tests/unit/test_workflow_model_routing.py
──────────────────────────────────────────
2026-07-03: Workflow-spezifisches Modell-Routing.

Grundlage (Modellvergleich Runde 2 + Referenzfall-Analyse):
  - gemma4:31b       fuer transkriptbasierte Doku/Anamnese/Entlassbericht
  - mistral-small3.2 fuer die redigiert-strukturierten Antraege

Aufloesungsreihenfolge (in jobs.create_generate_job):
  explizite Frontend-Wahl > WORKFLOW_MODEL-Map > OLLAMA_MODEL

Diese Tests sichern die Map + den Resolver. Die Vorrang-Logik (Frontend-Wahl
schlaegt Map) ist ein Einzeiler in jobs.py und wird dort durch die
bestehenden generate-Tests mit abgedeckt.
"""
from __future__ import annotations

from app.core.config import settings


class TestWorkflowModelMap:

    def test_doku_gruppe_gemma4(self):
        for wf in ("dokumentation", "anamnese", "entlassbericht"):
            assert settings.model_for_workflow(wf) == "gemma4:31b", wf

    def test_antrags_gruppe_mistral(self):
        for wf in ("akutantrag", "verlaengerung", "folgeverlaengerung"):
            assert settings.model_for_workflow(wf) == "mistral-small3.2", wf

    def test_unbekannter_workflow_faellt_auf_default(self):
        # 'befund' ist ein interner Sub-Workflow ohne eigenen Map-Eintrag
        assert settings.model_for_workflow("befund") == settings.OLLAMA_MODEL

    def test_none_faellt_auf_default(self):
        assert settings.model_for_workflow(None) == settings.OLLAMA_MODEL

    def test_leerer_string_faellt_auf_default(self):
        assert settings.model_for_workflow("") == settings.OLLAMA_MODEL

    def test_map_ohne_ollama_praefix(self):
        # Werte muessen reine Ollama-Tags sein (kein 'ollama/'), wie OLLAMA_MODEL
        for v in settings.WORKFLOW_MODEL.values():
            assert not v.startswith("ollama/"), v

    def test_alle_haupt_workflows_abgedeckt(self):
        # Die sechs frontend-waehlbaren Workflows haben alle einen Default
        for wf in ("dokumentation", "anamnese", "entlassbericht",
                   "akutantrag", "verlaengerung", "folgeverlaengerung"):
            assert wf in settings.WORKFLOW_MODEL, wf
