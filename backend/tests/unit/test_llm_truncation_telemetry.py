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
    async def test_mittlerer_input_reduziert_nur_output_budget(self, monkeypatch):
        """Input passt INS FENSTER, aber Input+max_tokens > MAX_SAFE_CTX ->
        max_tokens wird reduziert, Input bleibt vollstaendig.

        v19.5.1: MAX_SAFE_CTX = min(20480, LLM_NUM_CTX_CAP). Damit dieses
        Szenario (voller Input, nur Output schrumpft) UEBERHAUPT auftreten kann
        und der Input real ins num_ctx-Fenster passt, muss der Cap gross genug
        sein. Auf einer 32GB-GPU wird LLM_NUM_CTX_CAP=32768 gesetzt (q8_0-KV) ->
        hier deterministisch gepinnt.
        """
        from app.services.llm import generate_text

        monkeypatch.setattr(
            "app.services.llm.settings.LLM_NUM_CTX_CAP", 32768, raising=False
        )

        async def _fake(system_prompt, user_content, max_tokens, **kwargs):
            return _fake_ollama_result()

        # v19.19 (K1): Die harte Decke ist jetzt 32768 (vorher 20480), d.h.
        # MAX_SAFE_CTX = min(32768, 32768) = 32768.
        # ~29.7k geschaetzte Tokens: 104000 Zeichen. 29714 + 3500 > 32768,
        # aber 32768 - 29714 - 200 = 2854 >= 1000 (min_output dokumentation)
        # -> nur Output-Budget schrumpft, Input bleibt vollstaendig.
        # (Der alte 64k-Fall - 18.3k Tokens - passt mit 32k jetzt komplett
        # ohne Drosselung: genau das ist der K1-Gewinn, siehe Log-Analyse
        # 13.08.-09.09.: 15 von 19 Kuerzungen entfallen.)
        medium_user = ("Verlaufszeile mit Inhalt. " * 5000).strip()[:104000]

        with patch("app.services.llm._generate_ollama", _fake):
            result = await generate_text(
                "System.", medium_user,
                max_tokens=3500,
                workflow="dokumentation",
            )

        tel = result["telemetry"]
        assert tel["output_budget_reduced"] is True
        assert tel["input_truncated"] is False

    @pytest.mark.asyncio
    async def test_deadzone_input_wird_gekuerzt_statt_still_zu_ueberlaufen(
        self, monkeypatch
    ):
        """v19.5.1 Regression (Job 2922a9, prompts.log 2026-07-09):

        Ein Input im Bereich [LLM_NUM_CTX_CAP, 20480] Tokens (z.B. rohes
        ~8.500-Woerter-Transkript ≈ 17.3k Tokens) darf NICHT mehr voll durch-
        gereicht werden (das lief frueher ueber num_ctx=16384 und wurde von
        Ollama STILL abgeschnitten -> gemma-Verweigerung). Bei Default-Cap 16384
        koppelt der Fix MAX_SAFE_CTX an das Fenster -> der Input wird SICHTBAR
        gekuerzt (input_truncated=True) und passt garantiert ins Fenster.
        """
        from app.services.llm import generate_text

        monkeypatch.setattr(
            "app.services.llm.settings.LLM_NUM_CTX_CAP", 16384, raising=False
        )

        captured: dict = {}

        async def _fake(system_prompt, user_content, max_tokens, **kwargs):
            captured["user_len"] = len(user_content)
            return _fake_ollama_result()

        # ~17.3k geschaetzte Tokens: liegt zwischen Cap (16384) und altem
        # MAX_SAFE_CTX (20480) -> genau die frueher stille Overflow-Zone.
        deadzone_user = ("Transkriptzeile Inhalt. " * 2800).strip()[:60000]
        assert 55000 < len(deadzone_user)  # ~17k Tokens

        with patch("app.services.llm._generate_ollama", _fake):
            result = await generate_text(
                "System.", deadzone_user,
                max_tokens=3500,
                workflow="dokumentation",
            )

        tel = result["telemetry"]
        assert tel["input_truncated"] is True
        assert captured["user_len"] < len(deadzone_user)


class TestRefusalDetection:
    """v19.5.1: Meta-Verweigerungen ("Bitte stellen Sie mir das Transkript
    zur Verfuegung") muessen erkannt werden (loesen Retry/degraded aus),
    echte patientenbezogene Berichte duerfen NICHT matchen."""

    def test_echte_verweigerung_wird_erkannt(self):
        from app.services.llm import _looks_like_refusal

        refusal = (
            "Auftragsklärung\n\nBitte stellen Sie mir die entsprechenden Quellen "
            "(Transkript, Stichpunkte oder Verlaufsnotizen) zur Verfügung, damit "
            "ich die Gesprächsdokumentation für Herrn W. auf Basis der "
            "tatsächlichen Inhalte erstellen kann. Sobald mir das Material "
            "vorliegt, beginne ich sofort mit dem Schreiben des Berichts."
        )
        assert _looks_like_refusal(refusal) is True

    def test_weitere_verweigerungs_formeln(self):
        from app.services.llm import _looks_like_refusal

        assert _looks_like_refusal(
            "Mir liegt kein Transkript vor. Bitte übermitteln Sie mir das Protokoll."
        ) is True
        assert _looks_like_refusal(
            "Um die Dokumentation zu erstellen, benötige ich noch das Transkript."
        ) is True

    def test_echter_bericht_matcht_nicht(self):
        from app.services.llm import _looks_like_refusal

        report = (
            "Auftragsklärung\n\nIm Mittelpunkt stand das Anspannungserleben von "
            "Herrn W. vor familiären Begegnungen. Herr W. wurde eingeladen, seinem "
            "Vater eine Nachricht zu senden und offene Fragen direkt zu stellen. Als "
            "Übung wurde vereinbart, die zur Verfügung stehende Zeit zu reflektieren."
        )
        assert _looks_like_refusal(report) is False
