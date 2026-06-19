"""
Tests fuer den v19.1 Retry-Layer in app/services/llm.py.

Schwerpunkte:
  - _compute_telemetry (Think-Block-Detection)
  - _is_output_implausibly_short (Detection-Logik fuer den Retry)
  - _get_model_profile (Modell-Prefix-Match)

Reine Python-Funktionen — kein LLM-Aufruf noetig.
"""
import pytest

from app.services.llm import (
    _compute_telemetry,
    _is_output_implausibly_short,
    _get_model_profile,
    MODEL_PROFILES,
)


# ─────────────────────────────────────────────────────────────────────────────
# _compute_telemetry: Think-Block-Detection
# ─────────────────────────────────────────────────────────────────────────────


class TestComputeTelemetry:

    def test_kein_think_block_alles_null(self):
        t = _compute_telemetry("Normaler Text ohne Tags.", eval_count=42, max_tokens=2000)
        assert t["think_length"] == 0
        assert t["think_ratio"] == 0.0
        assert t["had_orphan_think_open"] is False
        assert t["had_orphan_think_close"] is False

    def test_kompletter_think_block(self):
        raw = "<think>internal reasoning</think>Eigentlicher Output."
        t = _compute_telemetry(raw, eval_count=10, max_tokens=2000)
        assert t["think_length"] > 0
        assert t["think_ratio"] > 0
        assert t["had_orphan_think_open"] is False
        assert t["had_orphan_think_close"] is False

    def test_offener_think_block_ohne_close(self):
        """<think> ohne </think>: alles dahinter ist Think (Budget aufgebraucht)."""
        raw = "<think>thinking forever and never producing output"
        t = _compute_telemetry(raw, eval_count=2000, max_tokens=2000)
        assert t["had_orphan_think_open"] is True
        assert t["had_orphan_think_close"] is False
        assert t["think_length"] > 0

    def test_close_ohne_open(self):
        raw = "Some text</think>Real output."
        t = _compute_telemetry(raw, eval_count=10, max_tokens=2000)
        assert t["had_orphan_think_close"] is True
        assert t["had_orphan_think_open"] is False

    def test_tokens_hit_cap_innerhalb_toleranz(self):
        """eval_count >= max_tokens - 50 = Cap erreicht."""
        # 1980 >= 2000 - 50 = 1950 -> True
        t = _compute_telemetry("text", eval_count=1980, max_tokens=2000)
        assert t["tokens_hit_cap"] is True

    def test_tokens_klar_unter_cap(self):
        t = _compute_telemetry("text", eval_count=500, max_tokens=2000)
        assert t["tokens_hit_cap"] is False

    def test_used_thinking_fallback_propagiert(self):
        t = _compute_telemetry("text", eval_count=10, max_tokens=2000,
                               used_thinking_fallback=True)
        assert t["used_thinking_fallback"] is True

    def test_raw_length_korrekt(self):
        raw = "Hello World"
        t = _compute_telemetry(raw, eval_count=1, max_tokens=100)
        assert t["raw_length"] == len(raw)

    def test_eval_count_propagiert(self):
        t = _compute_telemetry("text", eval_count=123, max_tokens=2000)
        assert t["eval_count"] == 123

    def test_think_ratio_realistisch(self):
        """Wenn der gesamte Output ein Think-Block ist: ratio ≈ 1.0."""
        raw = "<think>" + "long thinking " * 50 + "</think>X"
        t = _compute_telemetry(raw, eval_count=200, max_tokens=2000)
        assert t["think_ratio"] > 0.9


# ─────────────────────────────────────────────────────────────────────────────
# _is_output_implausibly_short
# ─────────────────────────────────────────────────────────────────────────────


class TestIsOutputImplausiblyShort:

    def test_leerer_output_immer_zu_kurz(self):
        too_short, reason = _is_output_implausibly_short(
            workflow="entlassbericht",
            final_text="",
            telemetry={},
        )
        assert too_short is True
        assert "leer" in reason.lower() or "komplett" in reason.lower()

    def test_langer_output_nie_zu_kurz(self):
        too_short, _ = _is_output_implausibly_short(
            workflow="entlassbericht",
            final_text=" ".join(["wort"] * 1000),  # 1000 Woerter
            telemetry={"think_ratio": 0.5},
        )
        assert too_short is False

    def test_kurzer_output_OHNE_think_indikatoren_nicht_zu_kurz(self):
        """Beide Bedingungen muessen gelten: kurz UND verdaechtige Telemetrie."""
        too_short, _ = _is_output_implausibly_short(
            workflow="entlassbericht",
            final_text="Sehr kurzer Text.",
            telemetry={
                "think_ratio": 0.1,
                "tokens_hit_cap": False,
                "had_orphan_think_open": False,
                "had_orphan_think_close": False,
                "used_thinking_fallback": False,
            },
        )
        assert too_short is False  # darf nicht als degraded klassifiziert werden

    def test_kurz_MIT_high_think_ratio_ist_zu_kurz(self):
        too_short, reason = _is_output_implausibly_short(
            workflow="entlassbericht",
            final_text="Sehr kurzer Text.",
            telemetry={
                "think_ratio": 0.5,
                "tokens_hit_cap": False,
                "had_orphan_think_open": False,
                "had_orphan_think_close": False,
                "used_thinking_fallback": False,
            },
        )
        assert too_short is True
        assert reason

    def test_kurz_MIT_tokens_hit_cap_ist_zu_kurz(self):
        too_short, _ = _is_output_implausibly_short(
            workflow="entlassbericht",
            final_text="Kurz.",
            telemetry={
                "think_ratio": 0.0,
                "tokens_hit_cap": True,
                "had_orphan_think_open": False,
                "had_orphan_think_close": False,
                "used_thinking_fallback": False,
            },
        )
        assert too_short is True

    def test_kurz_MIT_orphan_think_open_ist_zu_kurz(self):
        too_short, _ = _is_output_implausibly_short(
            workflow="entlassbericht",
            final_text="Kurz.",
            telemetry={
                "think_ratio": 0.0,
                "tokens_hit_cap": False,
                "had_orphan_think_open": True,
                "had_orphan_think_close": False,
                "used_thinking_fallback": False,
            },
        )
        assert too_short is True

    def test_workflow_spezifische_schwellen(self):
        """entlassbericht hat threshold=300w, akutantrag=100w."""
        text_150w = " ".join(["wort"] * 150)
        suspicious_telem = {
            "think_ratio": 0.5,
            "tokens_hit_cap": False,
            "had_orphan_think_open": False,
            "had_orphan_think_close": False,
            "used_thinking_fallback": False,
        }

        # entlassbericht: 150w < 300w threshold + verdaechtig = zu kurz
        too_short_eb, _ = _is_output_implausibly_short(
            workflow="entlassbericht",
            final_text=text_150w,
            telemetry=suspicious_telem,
        )
        assert too_short_eb is True

        # akutantrag: 150w > 100w threshold = nicht zu kurz
        too_short_akut, _ = _is_output_implausibly_short(
            workflow="akutantrag",
            final_text=text_150w,
            telemetry=suspicious_telem,
        )
        assert too_short_akut is False

    def test_unbekannter_workflow_default_100(self):
        text_50w = " ".join(["w"] * 50)
        too_short, _ = _is_output_implausibly_short(
            workflow="foo",
            final_text=text_50w,
            telemetry={
                "think_ratio": 0.5,
                "tokens_hit_cap": False,
                "had_orphan_think_open": False,
                "had_orphan_think_close": False,
                "used_thinking_fallback": False,
            },
        )
        assert too_short is True  # 50w < 100w default


# ─────────────────────────────────────────────────────────────────────────────
# _get_model_profile
# ─────────────────────────────────────────────────────────────────────────────


class TestGetModelProfile:

    @pytest.mark.parametrize("name,expected_prefix", [
        ("qwen3:32b",        "qwen3"),
        ("qwen3:32b-q4_K_M", "qwen3"),
        ("qwen3:30b-a3b",    "qwen3"),
        ("qwen2.5:32b",      "qwen2.5"),
        ("deepseek-r1",      "deepseek-r1"),  # exakter Match vor 'deepseek'
        ("deepseek-coder",   "deepseek"),
        ("llama3.1:8b",      "llama"),
        ("gemma2:9b",        "gemma"),
        ("mistral-nemo",     "mistral"),
    ])
    def test_matched_profiles(self, name, expected_prefix):
        profile = _get_model_profile(name)
        # Pruefe dass exakt das matchende Profil zurueckgegeben wird
        assert profile is MODEL_PROFILES[expected_prefix]

    def test_unbekannter_name_default(self):
        assert _get_model_profile("unknown-model:1b") is MODEL_PROFILES["_default"]

    def test_case_insensitive(self):
        assert _get_model_profile("QWEN3:32B") is MODEL_PROFILES["qwen3"]

    def test_qwen3_hat_andere_temperature_als_qwen25(self):
        """Regression: Qwen3 (0.4) und Qwen2.5 (0.3) muessen unterschiedlich bleiben."""
        assert _get_model_profile("qwen3:32b")["temperature"] == 0.4
        assert _get_model_profile("qwen2.5:32b")["temperature"] == 0.3


# ─────────────────────────────────────────────────────────────────────────────
# v19.5: _repeat_sampling_opts (Wiederholungs-Penalty gegen Degeneration)
# ─────────────────────────────────────────────────────────────────────────────

from app.services.llm import (  # noqa: E402
    _repeat_sampling_opts,
    DEFAULT_REPEAT_PENALTY,
    DEFAULT_REPEAT_LAST_N,
)


class TestRepeatSamplingOpts:

    def test_qwen3_profil_setzt_explizite_werte(self):
        prof = _get_model_profile("qwen3:32b")
        opts = _repeat_sampling_opts(prof)
        assert opts["repeat_penalty"] == 1.18
        assert opts["repeat_last_n"] == 512

    def test_profil_ohne_werte_faellt_auf_default(self):
        # _default-Profil hat keine repeat_* Keys -> globale Defaults.
        prof = _get_model_profile("voellig-unbekanntes-modell")
        opts = _repeat_sampling_opts(prof)
        assert opts["repeat_penalty"] == DEFAULT_REPEAT_PENALTY
        assert opts["repeat_last_n"] == DEFAULT_REPEAT_LAST_N

    def test_settings_override_schlaegt_profil(self, monkeypatch):
        # Settings-Override (falls gesetzt) gewinnt selbst gegen das qwen3-Profil.
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_REPEAT_PENALTY", 1.25, raising=False)
        monkeypatch.setattr(settings, "LLM_REPEAT_LAST_N", 1024, raising=False)
        opts = _repeat_sampling_opts(_get_model_profile("qwen3:32b"))
        assert opts["repeat_penalty"] == 1.25
        assert opts["repeat_last_n"] == 1024

    def test_none_settings_nutzen_profil(self, monkeypatch):
        # Default-Settings sind None -> Profilwert bleibt maßgeblich.
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_REPEAT_PENALTY", None, raising=False)
        monkeypatch.setattr(settings, "LLM_REPEAT_LAST_N", None, raising=False)
        opts = _repeat_sampling_opts(_get_model_profile("qwen3:32b"))
        assert opts["repeat_penalty"] == 1.18
        assert opts["repeat_last_n"] == 512


class TestSeedSamplingOpt:

    def test_kein_seed_per_default(self):
        # Prod-Default: LLM_SEED None -> kein seed-Key (nicht-deterministisch).
        from app.core.config import settings
        if getattr(settings, "LLM_SEED", None) is None:
            opts = _repeat_sampling_opts(_get_model_profile("qwen3:32b"))
            assert "seed" not in opts

    def test_seed_wird_uebernommen_wenn_gesetzt(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_SEED", 42, raising=False)
        opts = _repeat_sampling_opts(_get_model_profile("qwen3:32b"))
        assert opts["seed"] == 42
        # repeat_* bleiben unberuehrt
        assert opts["repeat_penalty"] == 1.18

    def test_seed_none_setzt_keinen_key(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_SEED", None, raising=False)
        opts = _repeat_sampling_opts(_get_model_profile("qwen3:32b"))
        assert "seed" not in opts


# ─────────────────────────────────────────────────────────────────────────────
# v19.5: konfigurierbarer num_ctx-Cap (Context-Bump auf faehiger Hardware)
# ─────────────────────────────────────────────────────────────────────────────

from app.services.llm import _estimate_num_ctx  # noqa: E402


class TestNumCtxCapConfigurable:

    SEHR_LANG = "x" * 200_000   # erzwingt den Cap

    def test_default_cap_16384(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_NUM_CTX_CAP", 16384, raising=False)
        assert _estimate_num_ctx("System.", self.SEHR_LANG, 2048) == 16384

    def test_cap_hochsetzbar_32768(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_NUM_CTX_CAP", 32768, raising=False)
        assert _estimate_num_ctx("System.", self.SEHR_LANG, 2048) == 32768

    def test_cap_aendert_kurzen_input_nicht(self, monkeypatch):
        # Kurzer Input bleibt klein, egal wie hoch der Cap steht.
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_NUM_CTX_CAP", 32768, raising=False)
        ctx = _estimate_num_ctx("System.", "Kurze Anfrage.", 512)
        assert ctx <= 4096

    def test_immer_vielfaches_von_512(self, monkeypatch):
        from app.core.config import settings
        monkeypatch.setattr(settings, "LLM_NUM_CTX_CAP", 32768, raising=False)
        for chars in [5000, 50000, 150000]:
            ctx = _estimate_num_ctx("S.", "x" * chars, 1024)
            assert ctx % 512 == 0
