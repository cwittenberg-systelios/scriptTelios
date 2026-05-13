"""
Unit-Tests fuer summarize_verlauf() — der Stage-1-Service-Entry-Point.

Mockt generate_text aus app.services.llm, damit kein echter Ollama-Call
noetig ist.
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.verlauf_summary import summarize_verlauf


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ok_result(words: int = 200, text: str | None = None) -> dict:
    """Baut ein Fake-llm-generate-Result mit N Woertern."""
    if text is None:
        text = " ".join(["wort"] * words)
    return {
        "text": text,
        "model_used": "ollama/qwen3:32b",
        "token_count": words,
        "telemetry": {"think_ratio": 0.0, "tokens_hit_cap": False},
        "duration_s": 5.0,
    }


def _faithful_summary(words: int = 200) -> str:
    """Eine quellentreue Demo-Summary (keine ICD/Verfahrens-Halluzinationen)."""
    base = (
        "### Sitzungsübersicht\n"
        "Drei Einzelgespraeche zwischen 01.03. und 10.03.\n\n"
        "### Bearbeitete Themen\n"
        "Selbstabwertung im Familienkontext.\n\n"
        "### Therapeutische Interventionen\n"
        "Es wurde an Beziehungsmustern gearbeitet.\n\n"
        "### Beobachtete Entwicklung\n"
        "Verlauf im Protokoll als stabilisierend beschrieben."
    )
    # Auf gewuenschte Wortzahl auffuellen
    current = len(base.split())
    if current < words:
        base += "\n\n" + " ".join(["fuelltext"] * (words - current))
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSummarizeVerlaufHappyPath:
    @pytest.mark.asyncio
    async def test_returns_summary_und_metadaten(self):
        verlauf = " ".join(["wort"] * 10000)  # 10k Woerter Input
        fake = _ok_result(words=4000, text=_faithful_summary(4000))

        with patch(
            "app.services.llm.generate_text",
            new=AsyncMock(return_value=fake),
        ):
            result = await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                patient_initial="M.",
                target_words=4000,
            )

        assert "summary" in result
        assert result["raw_word_count"] == 10000
        assert result["summary_word_count"] >= 200
        assert 0 < result["compression_ratio"] < 1
        assert result["retry_used"] is False
        assert result["degraded"] is False
        assert isinstance(result["issues"], list)

    @pytest.mark.asyncio
    async def test_kompressions_ratio_macht_sinn(self):
        verlauf = " ".join(["wort"] * 10000)
        # Summary 4000 Woerter
        fake = _ok_result(words=4000, text=_faithful_summary(4000))
        with patch(
            "app.services.llm.generate_text",
            new=AsyncMock(return_value=fake),
        ):
            result = await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                target_words=4000,
            )
        # 4000 / 10000 = 0.4
        assert 0.3 < result["compression_ratio"] < 0.6


class TestSummarizeVerlaufPlausibility:
    @pytest.mark.asyncio
    async def test_zu_kurzer_output_raises(self):
        verlauf = " ".join(["wort"] * 10000)
        # Nur 5 Woerter Output → unter 40% von 4000 = 1600
        fake = _ok_result(words=5, text="nur fuenf woerter im output text")
        with patch(
            "app.services.llm.generate_text",
            new=AsyncMock(return_value=fake),
        ):
            with pytest.raises(RuntimeError, match="implausibel kurz"):
                await summarize_verlauf(
                    verlauf_text=verlauf,
                    workflow="verlaengerung",
                    target_words=4000,
                )

    @pytest.mark.asyncio
    async def test_leerer_output_raises(self):
        verlauf = " ".join(["wort"] * 10000)
        fake = _ok_result(words=0, text="")
        with patch(
            "app.services.llm.generate_text",
            new=AsyncMock(return_value=fake),
        ):
            with pytest.raises(RuntimeError, match="leer"):
                await summarize_verlauf(
                    verlauf_text=verlauf,
                    workflow="verlaengerung",
                    target_words=4000,
                )

    @pytest.mark.asyncio
    async def test_zu_langer_output_warned_aber_kein_fail(self, caplog):
        verlauf = " ".join(["wort"] * 10000)
        # 9000 Woerter Output > 2x Target (4000*2=8000)
        big_text = _faithful_summary(9000)
        fake = _ok_result(words=9000, text=big_text)
        with patch(
            "app.services.llm.generate_text",
            new=AsyncMock(return_value=fake),
        ):
            result = await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                target_words=4000,
            )
        # Kein Fail, aber result da
        assert result["summary_word_count"] > 4000

    @pytest.mark.asyncio
    async def test_leerer_verlauf_input_raises(self):
        with pytest.raises(RuntimeError, match="leerer Verlauf"):
            await summarize_verlauf(
                verlauf_text="",
                workflow="verlaengerung",
                target_words=4000,
            )

    @pytest.mark.asyncio
    async def test_whitespace_verlauf_input_raises(self):
        with pytest.raises(RuntimeError, match="leerer Verlauf"):
            await summarize_verlauf(
                verlauf_text="   \n\n   ",
                workflow="verlaengerung",
                target_words=4000,
            )


class TestSummarizeVerlaufPromptShape:
    """Pruefen dass system_prompt und user_content sinnvoll zusammengesetzt sind."""

    @pytest.mark.asyncio
    async def test_user_content_enthaelt_verlauf_und_marker(self):
        verlauf = "Realer Verlauf-Inhalt mit Markersignatur ABC123."
        fake = _ok_result(words=2000, text=_faithful_summary(2000))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_verlauf(
                verlauf_text=verlauf,
                workflow="verlaengerung",
                target_words=4000,
            )

        # Marker da
        assert ">>>VERLAUFSDOKU<<<" in calls["user_content"]
        assert "ABC123" in calls["user_content"]
        # workflow=None (kein BASE_PROMPT/Primer in Stage 1)
        assert calls["workflow"] is None

    @pytest.mark.asyncio
    async def test_patient_initial_im_user_content(self):
        fake = _ok_result(words=2000, text=_faithful_summary(2000))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_verlauf(
                verlauf_text="Verlauf-Text.",
                workflow="verlaengerung",
                patient_initial="v.M.",
                target_words=4000,
            )

        assert "v.M." in calls["user_content"]
        assert "AKTUELLER PATIENT:" in calls["user_content"]

    @pytest.mark.asyncio
    async def test_low_temperature_override(self):
        fake = _ok_result(words=2000, text=_faithful_summary(2000))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_verlauf(
                verlauf_text="Verlauf-Text.",
                workflow="verlaengerung",
                target_words=4000,
            )

        # 0.2 ist deutlich niedriger als der Default (qwen3 = 0.4)
        assert calls.get("temperature_override") == 0.2

    @pytest.mark.asyncio
    async def test_focus_hint_im_system_prompt_bei_verlaengerung(self):
        fake = _ok_result(words=2000, text=_faithful_summary(2000))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_verlauf(
                verlauf_text="Verlauf-Text.",
                workflow="verlaengerung",
                target_words=4000,
            )

        # Verlaengerungs-Hint muss im system_prompt drin sein
        assert "FOCUS:" in calls["system_prompt"]
        assert "Verlängerungsantrag" in calls["system_prompt"]

    @pytest.mark.asyncio
    async def test_kein_focus_hint_bei_unbekanntem_workflow(self):
        fake = _ok_result(words=2000, text=_faithful_summary(2000))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_verlauf(
                verlauf_text="Verlauf-Text.",
                workflow="akutantrag",  # nicht in der Whitelist
                target_words=4000,
            )

        assert "FOCUS:" not in calls["system_prompt"]
