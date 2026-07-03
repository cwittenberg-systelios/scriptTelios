"""
tests/unit/prompts/test_institutionelles_glossar.py
────────────────────────────────────────────────────
2026-07-03 (Live-Fund Herr W.): institutionelle Klinikbegriffe
("Familiengespräch" etc.) sind fuer ein LLM ungewoehnlich, obwohl im
Klinikkontext gebraeuchlich. Zwei getrennte Hebel:

  1. WHISPER_INITIAL_PROMPT (transcription.py) - bessere Erkennung an der
     Wurzel, falls Whisper selbst halluziniert/verhoert.
  2. INSTITUTIONELLES_GLOSSAR (prompts.py) - erklaert dem Text-LLM Begriffe,
     die bereits korrekt transkribiert, aber ungewoehnlich sind.

Diese Tests sichern die STRUKTUR ab (leerer dict -> sauberes Verhalten,
gefuellter dict -> korrekt im Prompt), nicht eine bestimmte Wortliste -
die wird von c.wittenberg laufend erweitert.
"""
from __future__ import annotations

from app.services.prompts import (
    INSTITUTIONELLES_GLOSSAR,
    _render_institutionelles_glossar,
    build_system_prompt,
)


class TestInstitutionellesGlossarRendering:

    def test_leerer_dict_ergibt_leeren_string(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.prompts.INSTITUTIONELLES_GLOSSAR", {}, raising=True
        )
        assert _render_institutionelles_glossar() == ""

    def test_gefuellter_dict_erscheint_im_block(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.prompts.INSTITUTIONELLES_GLOSSAR",
            {"Testbegriff": "eine Testerklärung"},
            raising=True,
        )
        block = _render_institutionelles_glossar()
        assert "Testbegriff" in block
        assert "eine Testerklärung" in block
        assert "KLINIK-ORGANISATORISCHE BEGRIFFE" in block

    def test_startbefuellung_vorhanden(self):
        # Die im Live-Fund konkret aufgetretenen Begriffe sind vorbefuellt
        assert "Familiengespräch" in INSTITUTIONELLES_GLOSSAR
        assert "Transfergespräch" in INSTITUTIONELLES_GLOSSAR


class TestInstitutionellesGlossarIntegration:

    def test_erscheint_im_system_prompt_unabhaengig_von_teilearbeit(self):
        # Institutionelles Glossar ist KEIN Priming-Risiko -> erscheint
        # sowohl bei neutraler als auch bei Teilearbeit-Quelle.
        neutral = build_system_prompt(
            workflow="dokumentation",
            source_text="Der Patient bespricht Termine.",
        )
        parts = build_system_prompt(
            workflow="dokumentation",
            source_text="Wir arbeiteten mit dem inneren Anteil.",
        )
        for prompt in (neutral, parts):
            assert "Familiengespräch" in prompt
            assert "KLINIK-ORGANISATORISCHE BEGRIFFE" in prompt

    def test_kein_platzhalter_rauschen_bei_leerem_glossar(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.prompts.INSTITUTIONELLES_GLOSSAR", {}, raising=True
        )
        prompt = build_system_prompt(workflow="dokumentation")
        assert "KLINIK-ORGANISATORISCHE BEGRIFFE" not in prompt


class TestWhisperInitialPromptErweiterung:

    def test_neue_institutionsbegriffe_enthalten(self):
        from app.services.transcription import WHISPER_INITIAL_PROMPT
        for term in ["Familiengespräch", "Transfergespräch", "Bezugsgruppe"]:
            assert term in WHISPER_INITIAL_PROMPT

    def test_bestehende_fachbegriffe_unveraendert_enthalten(self):
        # Regressionsschutz: bestehende Integration-Tests pruefen genau
        # diese Begriffe (tests/integration/test_suite.py::TestWhisperQualitaet)
        from app.services.transcription import WHISPER_INITIAL_PROMPT
        for term in ["Anteile", "Schutzmechanismus", "Schutzmuster",
                     "hypnosystemisch", "Anamnese", "Entlassbericht",
                     "Therapeut", "Klient"]:
            assert term in WHISPER_INITIAL_PROMPT
