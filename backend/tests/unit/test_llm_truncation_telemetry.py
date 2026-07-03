"""
tests/unit/test_llm_truncation_telemetry.py
───────────────────────────────────────────
O4-Fix (2026-07-01): stille Input-/Output-Budget-Kuerzungen in generate_text
muessen als Telemetrie-Flags sichtbar werden (input_truncated,
output_budget_reduced).

Hintergrund: Job 9b3b58 (prompts.log 2026-06-30) hatte ~18k Input-Tokens;
generate_text hat den Verlauf still per _sample_uniformly beschnitten
(MAX_SAFE_CTX=20480) - ohne Spur in Telemetrie oder Frontend.

Strategie: _generate_ollama wird gemockt (kein LLM, kein Netz), generate_text
laeuft komplett durch inkl. Budget-Logik und Flag-Injektion am Return.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

# 150 Woerter -> deutlich ueber jeder Plausibilitaetsschwelle,
# damit kein Think-Retry anspringt.
_LONG_OUTPUT = " ".join(f"Wort{i}" for i in range(150)) + "."


def _fake_ollama_result(text: str = _LONG_OUTPUT) -> dict:
    return {
        "text": text,
        "model_used": "ollama/qwen3:32b",
        "token_count": 200,
        "telemetry": {
            "raw_length": len(text),
            "think_length": 0,
            "think_ratio": 0.0,
            "had_orphan_think_open": False,
            "had_orphan_think_close": False,
            "tokens_hit_cap": False,
            "used_thinking_fallback": False,
            "eval_count": 200,
        },
    }


class TestTruncationTelemetry:

    @pytest.mark.asyncio
    async def test_kleiner_input_beide_flags_false(self):
        from app.services.llm import generate_text

        async def _fake(*args, **kwargs):
            return _fake_ollama_result()

        with patch("app.services.llm._generate_ollama", _fake):
            result = await generate_text(
                "Kurzer System-Prompt.", "Kurzer User-Content.",
                max_tokens=500,
            )

        tel = result["telemetry"]
        assert tel["input_truncated"] is False
        assert tel["output_budget_reduced"] is False

    @pytest.mark.asyncio
    async def test_riesiger_input_setzt_input_truncated(self):
        """Input so gross, dass selbst nach max_tokens-Reduktion nicht genug
        Platz bleibt -> User-Content wird gesampelt -> Flag MUSS True sein."""
        from app.services.llm import generate_text

        captured: dict = {}

        async def _fake(system_prompt, user_content, max_tokens, **kwargs):
            captured["user_len"] = len(user_content)
            return _fake_ollama_result()

        # ~24k geschaetzte Tokens Input (>> MAX_SAFE_CTX 20480):
        # 24000 * 3.5 = 84000 Zeichen
        huge_user = ("Verlaufszeile mit Inhalt. " * 4000).strip()
        assert len(huge_user) > 80000

        with patch("app.services.llm._generate_ollama", _fake):
            result = await generate_text(
                "System.", huge_user,
                max_tokens=3500,
                workflow="dokumentation",
            )

        tel = result["telemetry"]
        assert tel["input_truncated"] is True
        # Der tatsaechlich gesendete User-Content wurde gekuerzt
        assert captured["user_len"] < len(huge_user)

    @pytest.mark.asyncio
    async def test_mittlerer_input_reduziert_nur_output_budget(self):
        """Input passt, aber Input+max_tokens > MAX_SAFE_CTX -> max_tokens
        wird reduziert, Input bleibt vollstaendig."""
        from app.services.llm import generate_text

        async def _fake(system_prompt, user_content, max_tokens, **kwargs):
            return _fake_ollama_result()

        # ~18.3k geschaetzte Tokens (wie Job 9b3b58): 64000 Zeichen.
        # 18286 + 3500 > 20480, aber 20480 - 18286 - 200 = 1994 >= 1000
        # (min_output dokumentation) -> nur Output-Budget schrumpft.
        medium_user = ("Verlaufszeile mit Inhalt. " * 3000).strip()[:64000]

        with patch("app.services.llm._generate_ollama", _fake):
            result = await generate_text(
                "System.", medium_user,
                max_tokens=3500,
                workflow="dokumentation",
            )

        tel = result["telemetry"]
        assert tel["output_budget_reduced"] is True
        assert tel["input_truncated"] is False
