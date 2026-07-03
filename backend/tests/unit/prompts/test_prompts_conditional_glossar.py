"""
tests/unit/prompts/test_prompts_conditional_glossar.py
──────────────────────────────────────────────────────
Issue-2 (2026-07-03): Konditionales Glossar + Few-Shot.

Kernbefund (Eval + prompts.log e74be3): Die blosse AUFZAEHLUNG der
IFS-Begriffe im System-Prompt primt das Modell - es stuelpt sie
quellenfremden Faellen auf, die konditionale Regel verliert. Fix:
Begriffe stehen nur im Prompt, wenn die Quellen Teilearbeit enthalten.

Diese Tests frieren ein:
  1. Quellen OHNE Teilearbeit -> neutrales Glossar (keine IFS-Tokens),
     neutrales Doku-Beispiel.
  2. Quellen MIT Teilearbeit -> volles Glossar + IFS-Beispiel (korrekte
     Verwendung bleibt moeglich und angeleitet).
  3. Legacy-Aufruf ohne source_text -> altes Verhalten (volles Glossar),
     kein bestehender Test/Aufrufer bricht.
"""
from __future__ import annotations

from app.services.prompts import (
    build_system_prompt,
    source_mentions_parts_work,
    FEW_SHOT_DOKUMENTATION,
    FEW_SHOT_DOKUMENTATION_NEUTRAL,
)

# Tokens, die NUR im vollen Glossar/IFS-Beispiel vorkommen duerfen
_IFS_TOKENS = ["Manager", "Antreiber", "Verbannte", "Türsteher", "Wächterin",
               "Feuerbekämpfer", "Ego-State"]

_NEUTRAL_SOURCE = (
    "Transkript: Der Patient berichtet über Erschöpfung im Arbeitskontext, "
    "Schlafprobleme und Konflikte mit dem Vorgesetzten. Er beschreibt "
    "körperliche Anspannung im Nacken und den Wunsch nach besserer Abgrenzung."
)
_PARTS_SOURCE = (
    "Transkript: Wir arbeiteten mit dem inneren Anteil, der sich als "
    "Schutzschild zeigt. Die Patientin nahm Kontakt zu einem jüngeren "
    "Anteil auf."
)


class TestSourceDetection:

    def test_neutral_source_erkannt(self):
        assert source_mentions_parts_work(_NEUTRAL_SOURCE) is False

    def test_parts_source_erkannt(self):
        assert source_mentions_parts_work(_PARTS_SOURCE) is True

    def test_leere_quelle(self):
        assert source_mentions_parts_work("") is False

    def test_stems_case_insensitiv(self):
        assert source_mentions_parts_work("Arbeit mit dem TÜRSTEHER") is True
        assert source_mentions_parts_work("stuhlarbeit zum thema x") is True


class TestConditionalGlossar:

    def test_neutrale_quelle_keine_ifs_tokens(self):
        prompt = build_system_prompt(
            workflow="dokumentation", source_text=_NEUTRAL_SOURCE,
        )
        for tok in _IFS_TOKENS:
            assert tok not in prompt, f"IFS-Token '{tok}' primt trotz neutraler Quelle!"
        # Die Pro-Auftrag-Feststellung ist drin
        assert "enthalten KEINE Teilearbeit" in prompt
        # Neutrales Beispiel statt IFS-Beispiel
        assert "Manager-Anteil" not in prompt
        assert "automatisches Ja-Sagen" in prompt or "Ja-Sagen" in prompt

    def test_parts_quelle_volles_glossar_und_ifs_beispiel(self):
        prompt = build_system_prompt(
            workflow="dokumentation", source_text=_PARTS_SOURCE,
        )
        assert "Manager" in prompt          # volles Glossar
        assert "Manager-Anteil" in prompt   # IFS-Beispiel
        assert "enthalten KEINE Teilearbeit" not in prompt

    def test_legacy_ohne_source_text_unveraendert(self):
        # Kein source_text -> altes Verhalten (volles Glossar) - Rueckwaerts-
        # kompatibilitaet fuer alle bestehenden Aufrufer und Tests.
        prompt = build_system_prompt(workflow="dokumentation")
        assert "Manager" in prompt
        assert "Manager-Anteil" in prompt

    def test_andere_workflows_neutrale_quelle(self):
        # Glossar-Konditionalitaet gilt fuer ALLE Workflows; der Few-Shot-
        # Tausch nur fuer dokumentation (VA/EB-Beispiele bewusst unveraendert,
        # deren Quellen enthalten praktisch immer Teilearbeit).
        prompt = build_system_prompt(
            workflow="verlaengerung", source_text=_NEUTRAL_SOURCE,
        )
        assert "Verbannte" not in prompt
        assert "enthalten KEINE Teilearbeit" in prompt


class TestNeutralFewShotIntegrity:

    def test_neutral_beispiel_ist_ifs_frei(self):
        low = FEW_SHOT_DOKUMENTATION_NEUTRAL.lower()
        for stem in ("anteil", "ifs", "manager", "schutzschild", "im selbst"):
            assert stem not in low, f"Neutral-Beispiel enthaelt '{stem}'"

    def test_neutral_beispiel_hat_vier_sektionen(self):
        for sect in ("Auftragsklärung", "Relevante Gesprächsinhalte",
                     "Hypothesen und Entwicklungsperspektiven", "Einladungen"):
            assert sect in FEW_SHOT_DOKUMENTATION_NEUTRAL

    def test_original_beispiel_unveraendert_verfuegbar(self):
        assert "Manager-Anteil" in FEW_SHOT_DOKUMENTATION
