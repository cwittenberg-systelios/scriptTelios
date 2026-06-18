"""
Tests fuer app/services/staging.py.

Die Stage-1-Trigger-Logik (Verlauf + Transkript) hat genau eine Aufgabe:
unter welchen Bedingungen laeuft die Verdichtung? Frueher inline in
jobs.py und nur via Reproduktion testbar - mit dem Auszug in staging.py
werden alle Bedingungen direkt geprueft.
"""
import pytest

from app.services.staging import (
    STAGE1_VERLAUF_WORKFLOWS,
    STAGE1_VERLAUF_MIN_WORDS,
    STAGE1_TRANSCRIPT_WORKFLOWS,
    STAGE1_TRANSCRIPT_MIN_WORDS,
    should_run_verlauf_stage1,
    should_run_transcript_stage1,
    verlauf_stage1_skip_reason,
    transcript_stage1_skip_reason,
    compute_verlauf_target_words,
    compute_verlauf_min_acceptable,
    compute_transcript_target_words,
    compute_transcript_min_acceptable,
    compute_input_word_budget,
    plan_source_compression,
    MAX_SAFE_CTX,
)


# ─────────────────────────────────────────────────────────────────────────────
# should_run_verlauf_stage1
# ─────────────────────────────────────────────────────────────────────────────


class TestVerlaufStage1Trigger:

    @pytest.mark.parametrize("workflow", sorted(STAGE1_VERLAUF_WORKFLOWS))
    def test_passender_workflow_mit_genug_woertern_triggert(self, workflow):
        long_text = "Wort " * (STAGE1_VERLAUF_MIN_WORDS + 100)
        assert should_run_verlauf_stage1(workflow, long_text) is True

    @pytest.mark.parametrize("workflow", sorted(STAGE1_VERLAUF_WORKFLOWS))
    def test_passender_workflow_aber_zu_kurz_triggert_nicht(self, workflow):
        short_text = "Wort " * (STAGE1_VERLAUF_MIN_WORDS - 100)
        assert should_run_verlauf_stage1(workflow, short_text) is False

    @pytest.mark.parametrize("workflow", [
        "dokumentation", "anamnese", "akutantrag", "befund",
    ])
    def test_unpassender_workflow_triggert_nicht(self, workflow):
        long_text = "Wort " * (STAGE1_VERLAUF_MIN_WORDS + 100)
        assert should_run_verlauf_stage1(workflow, long_text) is False

    def test_flag_off_triggert_nicht(self):
        long_text = "Wort " * (STAGE1_VERLAUF_MIN_WORDS + 100)
        assert should_run_verlauf_stage1(
            "entlassbericht", long_text, flag_enabled=False,
        ) is False

    def test_leerer_text_triggert_nicht(self):
        assert should_run_verlauf_stage1("entlassbericht", "") is False
        assert should_run_verlauf_stage1("entlassbericht", None) is False
        assert should_run_verlauf_stage1("entlassbericht", "   ") is False

    def test_genau_an_grenze_triggert(self):
        """Schwelle ist >= (nicht >): mit exakt MIN_WORDS Wortzahl triggert."""
        text = "Wort " * STAGE1_VERLAUF_MIN_WORDS
        assert should_run_verlauf_stage1("entlassbericht", text) is True

    def test_min_words_override(self):
        """min_words-Parameter ueberschreibt Konstante."""
        text = "Wort " * 100
        assert should_run_verlauf_stage1(
            "entlassbericht", text, min_words=50,
        ) is True
        assert should_run_verlauf_stage1(
            "entlassbericht", text, min_words=500,
        ) is False


# ─────────────────────────────────────────────────────────────────────────────
# verlauf_stage1_skip_reason
# ─────────────────────────────────────────────────────────────────────────────


class TestVerlaufStage1SkipReason:

    def test_workflow_nicht_in_whitelist_returns_none(self):
        """Fuer irrelevante Workflows: kein Audit-Eintrag, daher None."""
        assert verlauf_stage1_skip_reason("dokumentation", "x" * 10_000) is None
        assert verlauf_stage1_skip_reason("anamnese", "x" * 10_000) is None

    def test_flag_off_gibt_klare_begruendung(self):
        text = "Wort " * (STAGE1_VERLAUF_MIN_WORDS + 100)
        reason = verlauf_stage1_skip_reason(
            "entlassbericht", text, flag_enabled=False,
        )
        assert reason == "stage1_disabled"

    def test_zu_kurz_enthaelt_wortzahl(self):
        text = "Wort " * 500
        reason = verlauf_stage1_skip_reason("entlassbericht", text)
        assert reason and "verlauf_kurz" in reason
        assert "500" in reason

    def test_laeuft_returns_none(self):
        text = "Wort " * (STAGE1_VERLAUF_MIN_WORDS + 100)
        assert verlauf_stage1_skip_reason("entlassbericht", text) is None


# ─────────────────────────────────────────────────────────────────────────────
# should_run_transcript_stage1 + skip_reason
# ─────────────────────────────────────────────────────────────────────────────


class TestTranscriptStage1:

    @pytest.mark.parametrize("workflow", sorted(STAGE1_TRANSCRIPT_WORKFLOWS))
    def test_passender_workflow_mit_genug_woertern_triggert(self, workflow):
        text = "Wort " * (STAGE1_TRANSCRIPT_MIN_WORDS + 100)
        assert should_run_transcript_stage1(workflow, text) is True

    def test_anamnese_knapp_unter_alter_schwelle_triggert(self):
        """v19.4 Schwelle 2800 (vorher 5000/3500): An-Transkripte ~4500w sind drin."""
        text = "Wort " * 4500
        assert should_run_transcript_stage1("anamnese", text) is True

    @pytest.mark.parametrize("workflow", [
        "entlassbericht", "verlaengerung", "folgeverlaengerung", "akutantrag",
    ])
    def test_andere_workflows_nicht(self, workflow):
        text = "Wort " * (STAGE1_TRANSCRIPT_MIN_WORDS + 100)
        assert should_run_transcript_stage1(workflow, text) is False

    def test_flag_off(self):
        text = "Wort " * (STAGE1_TRANSCRIPT_MIN_WORDS + 100)
        assert should_run_transcript_stage1(
            "dokumentation", text, flag_enabled=False,
        ) is False

    def test_skip_reason_zu_kurz_enthaelt_wortzahlen(self):
        text = "Wort " * 1000
        reason = transcript_stage1_skip_reason("dokumentation", text)
        assert reason and "transkript_kurz_1000" in reason
        assert f"min_{STAGE1_TRANSCRIPT_MIN_WORDS}" in reason


# ─────────────────────────────────────────────────────────────────────────────
# compute_verlauf_target_words
# ─────────────────────────────────────────────────────────────────────────────


class TestComputeVerlaufTargetWords:

    def test_proportional_default(self):
        assert compute_verlauf_target_words(10000) == 1200  # 10000 * 0.12

    def test_unter_floor_wird_auf_floor_angehoben(self):
        assert compute_verlauf_target_words(1000) == 800  # max(800, 120)

    def test_grosser_input(self):
        # 12962 * 0.12 = 1555 (gerundet)
        result = compute_verlauf_target_words(12962)
        assert 1500 <= result <= 1560

    def test_zero(self):
        assert compute_verlauf_target_words(0) == 800

    def test_floor_override(self):
        assert compute_verlauf_target_words(1000, floor=400) == 400

    def test_ratio_override(self):
        assert compute_verlauf_target_words(10000, ratio=0.20) == 2000


class TestComputeVerlaufMinAcceptable:

    def test_default(self):
        # 1555 * 0.30 = 466
        assert 460 <= compute_verlauf_min_acceptable(1555) <= 470

    def test_floor(self):
        # 800 * 0.30 = 240 -> Floor 400
        assert compute_verlauf_min_acceptable(800) == 400

    def test_grosses_target(self):
        # 5000 * 0.30 = 1500
        assert compute_verlauf_min_acceptable(5000) == 1500


# ─────────────────────────────────────────────────────────────────────────────
# compute_transcript_*
# ─────────────────────────────────────────────────────────────────────────────


class TestComputeTranscriptTargetWords:

    def test_kleines_transkript(self):
        # raw=5000 * 0.20 = 1000
        assert compute_transcript_target_words(5000) == 1000

    def test_mittleres_transkript(self):
        # raw=7000 * 0.20 = 1400
        assert compute_transcript_target_words(7000) == 1400

    def test_cap_bei_grossen_inputs(self):
        # raw=10000 * 0.20 = 2000 -> Cap 1500
        assert compute_transcript_target_words(10000) == 1500
        # raw=50000 -> auch Cap 1500
        assert compute_transcript_target_words(50000) == 1500

    def test_floor_bei_sehr_kleinen_inputs(self):
        # raw=500 * 0.20 = 100 -> Floor 600
        assert compute_transcript_target_words(500) == 600


class TestComputeTranscriptMinAcceptable:

    def test_default(self):
        # 1000 * 0.40 = 400
        assert compute_transcript_min_acceptable(1000) == 400

    def test_floor(self):
        # 600 * 0.40 = 240 -> Floor 300
        assert compute_transcript_min_acceptable(600) == 300


# ─────────────────────────────────────────────────────────────────────────────
# v19.4: Kombiniertes Input-Budget (compute_input_word_budget / plan_source_compression)
# ─────────────────────────────────────────────────────────────────────────────

class TestInputWordBudget:

    def test_kleiner_systemprompt_grosses_budget(self):
        # anamnese: max_tokens=5500. MAX_SAFE_CTX - 5500 - sys - 512, /2.0.
        b = compute_input_word_budget("anamnese", system_prompt_chars=3500)
        # grobe Plausibilitaet: deutlich ueber dem Floor, unter MAX_SAFE_CTX/2
        assert 1500 < b < MAX_SAFE_CTX
        # monoton: groesserer System-Prompt -> kleineres Budget
        b2 = compute_input_word_budget("anamnese", system_prompt_chars=20000)
        assert b2 < b

    def test_floor_bei_riesigem_systemprompt(self):
        # absurd grosser System-Prompt -> Budget faellt auf Floor 1500
        b = compute_input_word_budget("anamnese", system_prompt_chars=500_000)
        assert b == 1500

    def test_workflow_mit_groesserem_output_hat_kleineres_budget(self):
        # entlassbericht (max_tokens 6000) < dokumentation (3500) reserviert mehr
        # Output -> kleineres Input-Budget bei gleichem System-Prompt.
        eb = compute_input_word_budget("entlassbericht", system_prompt_chars=3000)
        doku = compute_input_word_budget("dokumentation", system_prompt_chars=3000)
        assert eb < doku


class TestPlanSourceCompression:

    def test_alles_passt_leerer_plan(self):
        sources = [
            {"label": "a", "words": 500, "compressed": False, "compressible": True},
            {"label": "b", "words": 400, "compressed": False, "compressible": True},
        ]
        assert plan_source_compression(sources, budget_words=2000) == {}

    def test_groesste_unverdichtete_zuerst(self):
        # Summe 5000, Budget 3000 -> 2000 wegkuerzen. Groesste Quelle zuerst.
        sources = [
            {"label": "selbst", "words": 3000, "compressed": False, "compressible": True},
            {"label": "vorbef", "words": 2000, "compressed": False, "compressible": True},
        ]
        plan = plan_source_compression(sources, budget_words=3000)
        # "selbst" (3000w) wird angefasst, Floor 25% => min 750
        assert "selbst" in plan
        assert plan["selbst"] >= 750
        # genug aus selbst allein (3000-750=2250 removable > 2000 overshoot)
        assert "vorbef" not in plan

    def test_floor_wird_respektiert(self):
        # eine riesige Quelle, Budget winzig -> Target faellt auf Floor 25%.
        sources = [
            {"label": "x", "words": 10000, "compressed": False, "compressible": True},
        ]
        plan = plan_source_compression(sources, budget_words=500)
        assert plan["x"] == 2500  # 10000 * 0.25 Floor

    def test_verdichtete_nur_als_letzte_reserve(self):
        # Rohquelle reicht nicht (Floor), dann wird die verdichtete gestrafft.
        sources = [
            {"label": "roh", "words": 2000, "compressed": False, "compressible": True},
            {"label": "summary", "words": 4000, "compressed": True, "compressible": True},
        ]
        plan = plan_source_compression(sources, budget_words=2000)
        # roh zuerst (Floor 25% = 500 -> 1500 removable)
        assert plan["roh"] == 500
        # overshoot 4000; nach roh noch 2500 -> summary wird gestrafft (Floor 45%)
        assert "summary" in plan
        assert plan["summary"] >= int(4000 * 0.45)

    def test_nicht_komprimierbare_bleiben_unangetastet(self):
        sources = [
            {"label": "fix", "words": 5000, "compressed": False, "compressible": False},
        ]
        # nichts verdichtbar -> leerer Plan trotz Ueberschreitung
        assert plan_source_compression(sources, budget_words=1000) == {}
