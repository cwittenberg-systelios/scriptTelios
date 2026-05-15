"""
Unit-Tests fuer summarize_transcript() — der Stage-1-Service fuer
Sitzungstranskripte (v19.3).

Mockt generate_text aus app.services.llm, damit kein echter Ollama-Call
noetig ist. Analog zu test_verlauf_summary_service.py.

Die Halluzinations-Detektion wird aus verlauf_summary.detect_summary_hallucination_signals
wiederverwendet (siehe transcript_summary.py); die zugehoerigen Tests sind
in test_verlauf_summary_halluzinations.py und werden hier nicht verdoppelt.
Diese Datei testet nur das Zusammenspiel: wie reagiert die Stage-1-Pipeline
auf gefundene Issues (Retry, degraded, etc.).
"""
import pytest
from unittest.mock import AsyncMock, patch

from app.services.transcript_summary import (
    summarize_transcript,
    TRANSCRIPT_SUMMARY_SYSTEM_PROMPT,
    TRANSCRIPT_SUMMARY_STRUCTURE,
    _wir_hint,
    WIR_WORKFLOWS,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _ok_result(words: int = 1000, text: str | None = None) -> dict:
    """Baut ein Fake-generate_text-Result mit N Woertern."""
    if text is None:
        text = " ".join(["wort"] * words)
    return {
        "text": text,
        "model_used": "ollama/qwen3:32b",
        "token_count": words,
        "telemetry": {"think_ratio": 0.0, "tokens_hit_cap": False},
        "duration_s": 5.0,
    }


def _faithful_summary(words: int = 1000) -> str:
    """Eine plausibel strukturierte 3-Sektionen-Verdichtung."""
    base = (
        "### 1. Auftragsklärung & Hauptanliegen\n"
        "Frau S. kommt mit anhaltender Erschoepfung und Schlafstoerungen.\n\n"
        "### 2. Verlauf & inhaltliche Schwerpunkte\n"
        "Im Verlauf der Sitzung wurden Familiendynamik und Schlafhygiene "
        "besprochen. Frau S. berichtet von Konflikten mit dem Partner.\n\n"
        "### 3. Vereinbarungen, Einladungen & Befund-relevantes\n"
        "Vereinbart wurde ein Schlaf-Tagebuch fuer zwei Wochen. "
        "Stimmung im Verlauf gedrueckt, Antrieb reduziert. "
        "Keine Suizidalitaet angesprochen."
    )
    current = len(base.split())
    if current < words:
        base += "\n\n" + " ".join(["fuelltext"] * (words - current))
    return base


def _raw_transcript(words: int) -> str:
    """Synthetisches Rohtranskript mit Sprecher-Markern."""
    chunks = []
    for i in range(words // 8):
        chunks.append(f"[A]: wort wort wort wort.\n[B]: wort wort wort.")
    text = "\n".join(chunks)
    # Ggf. auf exakte Wortzahl auffuellen
    cur = len(text.split())
    if cur < words:
        text += "\n" + " ".join(["fuelltext"] * (words - cur))
    return text


# ─────────────────────────────────────────────────────────────────────────────
# Stil-Hinweis
# ─────────────────────────────────────────────────────────────────────────────


class TestWirHint:
    def test_dokumentation_neutraler_stil(self):
        hint = _wir_hint("dokumentation")
        assert "neutral" in hint.lower() or "berichtet" in hint.lower()
        assert "Wir-Stil" not in hint

    def test_anamnese_neutraler_stil(self):
        hint = _wir_hint("anamnese")
        assert "Wir-Stil" not in hint

    def test_verlaengerung_wir_stil(self):
        # Future-Proofing: falls Whitelist erweitert wird
        hint = _wir_hint("verlaengerung")
        assert "Wir-Stil" in hint

    def test_entlassbericht_wir_stil(self):
        hint = _wir_hint("entlassbericht")
        assert "Wir-Stil" in hint

    def test_none_neutraler_stil(self):
        hint = _wir_hint(None)
        assert "Wir-Stil" not in hint

    def test_wir_workflows_konsistenz(self):
        # Alle Antrags-Workflows sind WIR
        for wf in ("akutantrag", "verlaengerung", "folgeverlaengerung", "entlassbericht"):
            assert wf in WIR_WORKFLOWS
        # dokumentation / anamnese sind NICHT WIR
        assert "dokumentation" not in WIR_WORKFLOWS
        assert "anamnese" not in WIR_WORKFLOWS


# ─────────────────────────────────────────────────────────────────────────────
# Prompts
# ─────────────────────────────────────────────────────────────────────────────


class TestPrompts:
    def test_system_prompt_enthaelt_kernregeln(self):
        p = TRANSCRIPT_SUMMARY_SYSTEM_PROMPT
        assert "VERDICHTEN, NICHT ERFINDEN" in p
        assert "KEINE INTERPRETATION" in p
        assert "KEINE WERTUNG" in p
        assert "Initiale" in p

    def test_struktur_hat_drei_sektionen(self):
        s = TRANSCRIPT_SUMMARY_STRUCTURE
        assert "### 1. Auftragsklärung" in s
        assert "### 2. Verlauf" in s
        assert "### 3. Vereinbarungen" in s

    def test_struktur_erwaehnt_amdp(self):
        # Section 3 muss AMDP-relevante Beobachtungen explizit nennen
        assert "AMDP" in TRANSCRIPT_SUMMARY_STRUCTURE


# ─────────────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────────────


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_returns_summary_und_metadaten(self):
        raw = _raw_transcript(7000)
        fake = _ok_result(words=1400, text=_faithful_summary(1400))

        with patch(
            "app.services.llm.generate_text",
            new=AsyncMock(return_value=fake),
        ):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
                patient_initial="v.S.",
            )

        assert "summary" in result
        assert result["raw_word_count"] >= 7000
        assert result["summary_word_count"] >= 600
        assert 0 < result["compression_ratio"] < 1
        assert result["retry_used"] is False
        assert result["degraded"] is False
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_audit_dict_struktur_vollstaendig(self):
        raw = _raw_transcript(6000)
        fake = _ok_result(words=1200, text=_faithful_summary(1200))

        with patch(
            "app.services.llm.generate_text",
            new=AsyncMock(return_value=fake),
        ):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        # Alle vom jobs.py-Block erwarteten Keys muessen vorhanden sein
        expected_keys = {
            "summary", "raw_word_count", "summary_word_count",
            "compression_ratio", "duration_s", "telemetry",
            "retry_telemetry", "retry_used", "degraded", "issues",
            "target_words", "min_acceptable",
        }
        assert expected_keys.issubset(result.keys())


# ─────────────────────────────────────────────────────────────────────────────
# Target-Words-Berechnung
# ─────────────────────────────────────────────────────────────────────────────


class TestTargetWordsProportional:
    @pytest.mark.asyncio
    async def test_proportional_20_prozent(self):
        # 7000w * 0.20 = 1400w (unter dem 1500w Hard-Cap)
        raw = _raw_transcript(7000)
        fake = _ok_result(words=1400, text=_faithful_summary(1400))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        # raw = 7000w (oder leicht mehr durch Sprecher-Marker)
        # target sollte ~1400w sein (unter Hard-Cap)
        assert 1300 <= result["target_words"] <= 1500

    @pytest.mark.asyncio
    async def test_hard_cap_1500_bei_grossem_input(self):
        # 10000w * 0.20 = 2000w → Hard-Cap 1500 greift
        raw = _raw_transcript(10000)
        fake = _ok_result(words=1500, text=_faithful_summary(1500))

        with patch(
            "app.services.llm.generate_text",
            new=AsyncMock(return_value=fake),
        ):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        assert result["target_words"] == 1500

    @pytest.mark.asyncio
    async def test_floor_600_bei_kleinem_input(self):
        # 2000w * 0.20 = 400w → Floor 600 greift (theoretisch; in der Praxis
        # blockiert _TRANSCRIPT_STAGE1_MIN_WORDS=5000 schon im jobs.py).
        raw = _raw_transcript(2000)
        fake = _ok_result(words=600, text=_faithful_summary(600))

        with patch(
            "app.services.llm.generate_text",
            new=AsyncMock(return_value=fake),
        ):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        assert result["target_words"] == 600

    @pytest.mark.asyncio
    async def test_target_words_override_wird_uebernommen(self):
        raw = _raw_transcript(8000)
        fake = _ok_result(words=900, text=_faithful_summary(900))

        with patch(
            "app.services.llm.generate_text",
            new=AsyncMock(return_value=fake),
        ):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
                target_words=900,
            )

        assert result["target_words"] == 900


# ─────────────────────────────────────────────────────────────────────────────
# Empty / Pathological Inputs
# ─────────────────────────────────────────────────────────────────────────────


class TestEmptyInputs:
    @pytest.mark.asyncio
    async def test_leeres_transkript_raises(self):
        with pytest.raises(RuntimeError, match="leeres Transkript"):
            await summarize_transcript(
                transcript_text="",
                workflow="dokumentation",
            )

    @pytest.mark.asyncio
    async def test_whitespace_transkript_raises(self):
        with pytest.raises(RuntimeError, match="leeres Transkript"):
            await summarize_transcript(
                transcript_text="   \n\n   ",
                workflow="dokumentation",
            )

    @pytest.mark.asyncio
    async def test_leerer_llm_output_raises(self):
        raw = _raw_transcript(6000)
        fake = _ok_result(words=0, text="")

        with patch(
            "app.services.llm.generate_text",
            new=AsyncMock(return_value=fake),
        ):
            with pytest.raises(RuntimeError, match="leer"):
                await summarize_transcript(
                    transcript_text=raw,
                    workflow="dokumentation",
                )


# ─────────────────────────────────────────────────────────────────────────────
# Retry-Logik
# ─────────────────────────────────────────────────────────────────────────────


class TestRetryOnTooShort:
    @pytest.mark.asyncio
    async def test_retry_triggered_bei_zu_kurzem_output(self):
        """Erster Pass zu kurz → Retry mit gutem Output → Erfolg."""
        raw = _raw_transcript(7000)
        # Target ~1400, min_acceptable ~560. Erster Pass: 100w → trigger.
        too_short = _ok_result(words=100, text="zu kurz " * 50)
        good = _ok_result(words=1400, text=_faithful_summary(1400))
        mock = AsyncMock(side_effect=[too_short, good])

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        assert mock.call_count == 2
        assert result["retry_used"] is True
        assert result["degraded"] is False
        assert result["summary_word_count"] >= 600
        # Issues sollten leer sein nach erfolgreichem Retry
        assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_retry_call_hat_niedrigere_temperatur(self):
        """Retry-Call nutzt temperature_override=0.3 (vs 0.4 im Erst-Call)."""
        raw = _raw_transcript(7000)
        too_short = _ok_result(words=100, text="zu kurz " * 50)
        good = _ok_result(words=1400, text=_faithful_summary(1400))

        call_args_list = []

        async def _capture(**kwargs):
            call_args_list.append(kwargs)
            return too_short if len(call_args_list) == 1 else good

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        assert len(call_args_list) == 2
        assert call_args_list[0]["temperature_override"] == 0.4
        assert call_args_list[1]["temperature_override"] == 0.3

    @pytest.mark.asyncio
    async def test_retry_user_content_erwaehnt_mindest_wortzahl(self):
        raw = _raw_transcript(7000)
        too_short = _ok_result(words=100, text="zu kurz " * 50)
        good = _ok_result(words=1400, text=_faithful_summary(1400))

        call_args_list = []

        async def _capture(**kwargs):
            call_args_list.append(kwargs)
            return too_short if len(call_args_list) == 1 else good

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        retry_user = call_args_list[1]["user_content"]
        assert "MINDESTENS" in retry_user
        assert "zu kurz" in retry_user.lower()


class TestRetryFailureRaises:
    @pytest.mark.asyncio
    async def test_zwei_zu_kurze_outputs_raises_runtime_error(self):
        """Erster + zweiter Pass zu kurz → RuntimeError, jobs.py-Wrapper faengt."""
        raw = _raw_transcript(7000)
        too_short1 = _ok_result(words=100, text="zu kurz " * 50)
        too_short2 = _ok_result(words=50, text="immer noch zu kurz " * 10)
        mock = AsyncMock(side_effect=[too_short1, too_short2])

        with patch("app.services.llm.generate_text", new=mock):
            with pytest.raises(RuntimeError, match="implausibel kurz nach Retry"):
                await summarize_transcript(
                    transcript_text=raw,
                    workflow="dokumentation",
                )

        assert mock.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_call_wirft_exception(self):
        raw = _raw_transcript(7000)
        too_short = _ok_result(words=100, text="zu kurz " * 50)
        mock = AsyncMock(side_effect=[too_short, RuntimeError("Ollama down")])

        with patch("app.services.llm.generate_text", new=mock):
            with pytest.raises(RuntimeError, match="Retry-Call fehlgeschlagen"):
                await summarize_transcript(
                    transcript_text=raw,
                    workflow="dokumentation",
                )

        assert mock.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# Prompt-Shape: Inhalt von system_prompt + user_content
# ─────────────────────────────────────────────────────────────────────────────


class TestPromptShape:
    @pytest.mark.asyncio
    async def test_user_content_enthaelt_transkript_und_marker(self):
        raw = _raw_transcript(6000) + " SIGNATUR-ABC123"
        fake = _ok_result(words=1200, text=_faithful_summary(1200))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        assert ">>>TRANSKRIPT<<<" in calls["user_content"]
        assert ">>>/TRANSKRIPT<<<" in calls["user_content"]
        assert "SIGNATUR-ABC123" in calls["user_content"]
        # workflow=None an generate_text (kein BASE_PROMPT/Primer in Stage 1)
        assert calls["workflow"] is None

    @pytest.mark.asyncio
    async def test_no_think_doppelt_im_user_content(self):
        """Anti-Think v19.2.2: /no_think am Anfang UND Ende des user_content."""
        raw = _raw_transcript(6000)
        fake = _ok_result(words=1200, text=_faithful_summary(1200))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        uc = calls["user_content"]
        assert uc.startswith("/no_think")
        # /no_think auch ans Ende (llm.py haengt eh nochmal an, aber das ist
        # idempotent; defense in depth)
        assert uc.rstrip().endswith("/no_think")

    @pytest.mark.asyncio
    async def test_anti_think_im_system_prompt(self):
        raw = _raw_transcript(6000)
        fake = _ok_result(words=1200, text=_faithful_summary(1200))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        sp = calls["system_prompt"]
        assert "KEIN INNERES NACHDENKEN" in sp
        assert "### 1. Auftragsklärung" in sp

    @pytest.mark.asyncio
    async def test_patient_initial_im_user_content(self):
        raw = _raw_transcript(6000)
        fake = _ok_result(words=1200, text=_faithful_summary(1200))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
                patient_initial="v.M.",
            )

        assert "v.M." in calls["user_content"]
        assert "AKTUELLER PATIENT:" in calls["user_content"]

    @pytest.mark.asyncio
    async def test_workflow_kontext_im_user_content(self):
        raw = _raw_transcript(6000)
        fake = _ok_result(words=1200, text=_faithful_summary(1200))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_transcript(
                transcript_text=raw,
                workflow="anamnese",
            )

        assert "WORKFLOW-KONTEXT: anamnese" in calls["user_content"]

    @pytest.mark.asyncio
    async def test_skip_aggressive_dedup_true(self):
        """Stage-1 nutzt strict-mode-Dedup (skip_aggressive_dedup=True)."""
        raw = _raw_transcript(6000)
        fake = _ok_result(words=1200, text=_faithful_summary(1200))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        assert calls["skip_aggressive_dedup"] is True

    @pytest.mark.asyncio
    async def test_temperature_0_4(self):
        raw = _raw_transcript(6000)
        fake = _ok_result(words=1200, text=_faithful_summary(1200))
        calls = {}

        async def _capture(**kwargs):
            calls.update(kwargs)
            return fake

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        assert calls["temperature_override"] == 0.4


# ─────────────────────────────────────────────────────────────────────────────
# Halluzinations-Retry: wenn die Synthese ICDs/Verfahren erfindet
# (Detektion-Logik selbst ist in test_verlauf_summary_halluzinations.py
# getestet; hier nur die Interaktion mit dem Stage-1-Retry-Pfad).
# ─────────────────────────────────────────────────────────────────────────────


def _summary_with_invented_icd(words: int = 1200) -> str:
    """Verdichtung mit ICD-Code der nicht in der Quelle steht."""
    base = (
        "### 1. Auftragsklärung & Hauptanliegen\n"
        "Frau S. kommt mit anhaltender Erschoepfung.\n\n"
        "### 2. Verlauf & inhaltliche Schwerpunkte\n"
        "Diagnose F33.2 wurde im Verlauf gestellt. Frau S. berichtet von "
        "Schlafstoerungen und Familienkonflikten.\n\n"
        "### 3. Vereinbarungen, Einladungen & Befund-relevantes\n"
        "Schlaf-Tagebuch vereinbart. Stimmung gedrueckt."
    )
    current = len(base.split())
    if current < words:
        base += "\n\n" + " ".join(["fuelltext"] * (words - current))
    return base


def _summary_with_invented_verfahren(words: int = 1200) -> str:
    """Verdichtung mit Verfahren das nicht in der Quelle steht."""
    base = (
        "### 1. Auftragsklärung & Hauptanliegen\n"
        "Frau S. kommt mit Angstsymptomatik.\n\n"
        "### 2. Verlauf & inhaltliche Schwerpunkte\n"
        "Mit EMDR und Schematherapie wurde an traumatischen Erinnerungen "
        "gearbeitet. Frau S. berichtet von Entlastung.\n\n"
        "### 3. Vereinbarungen, Einladungen & Befund-relevantes\n"
        "Fortsetzung der Sitzungen vereinbart."
    )
    current = len(base.split())
    if current < words:
        base += "\n\n" + " ".join(["fuelltext"] * (words - current))
    return base


class TestHalluzinationsRetry:
    @pytest.mark.asyncio
    async def test_critical_icd_triggert_retry(self):
        """Initial mit erfundenem ICD → Retry → sauber → keine degraded-Flag."""
        # Quelle erwaehnt KEINE ICDs
        raw = (
            "[A]: Wie geht es Ihnen heute?\n"
            "[B]: Ich bin staendig muede und kann nicht schlafen.\n"
        ) * 800  # ~6400 Woerter
        bad = _ok_result(words=1200, text=_summary_with_invented_icd(1200))
        good = _ok_result(words=1200, text=_faithful_summary(1200))
        mock = AsyncMock(side_effect=[bad, good])

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        assert mock.call_count == 2
        assert result["retry_used"] is True
        assert result["degraded"] is False
        # In der finalen Summary kein erfundener ICD mehr
        assert "F33.2" not in result["summary"]
        # Issues spiegeln den finalen (sauberen) Pass
        criticals = [i for i in result["issues"] if i["severity"] == "critical"]
        assert criticals == []

    @pytest.mark.asyncio
    async def test_critical_verfahren_triggert_retry(self):
        """Initial mit erfundenem Verfahren (EMDR) → Retry → sauber."""
        raw = (
            "[A]: Wir machen eine Atemuebung.\n"
            "[B]: Okay.\n"
        ) * 800
        bad = _ok_result(words=1200, text=_summary_with_invented_verfahren(1200))
        good = _ok_result(words=1200, text=_faithful_summary(1200))
        mock = AsyncMock(side_effect=[bad, good])

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        # Verfahren-Halluzination ist "high", nicht "critical" — sollte
        # also KEINEN Retry triggern. Verifiziere das.
        assert mock.call_count == 1
        assert result["retry_used"] is False
        # Aber das Issue ist da
        verfahren_issues = [
            i for i in result["issues"] if i["type"] == "verfahren_halluzination"
        ]
        assert len(verfahren_issues) >= 1

    @pytest.mark.asyncio
    async def test_retry_kann_critical_nicht_beheben_degraded(self):
        """Initial + Retry beide mit critical ICD → degraded=True, retry-Output."""
        raw = "[A]: Hallo.\n[B]: Hi.\n" * 800
        bad1 = _ok_result(words=1200, text=_summary_with_invented_icd(1200))
        bad2 = _ok_result(words=1200, text=_summary_with_invented_icd(1200))
        mock = AsyncMock(side_effect=[bad1, bad2])

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        assert mock.call_count == 2
        assert result["retry_used"] is True
        assert result["degraded"] is True
        # Output ist nicht leer — Aufrufer kann entscheiden
        assert result["summary_word_count"] > 0

    @pytest.mark.asyncio
    async def test_retry_system_prompt_erwaehnt_hallu_issues(self):
        """Bei critical Hallu wird der Retry-System-Prompt mit Hinweis ergaenzt."""
        raw = "[A]: Hallo.\n[B]: Hi.\n" * 800
        bad = _ok_result(words=1200, text=_summary_with_invented_icd(1200))
        good = _ok_result(words=1200, text=_faithful_summary(1200))

        call_args_list = []

        async def _capture(**kwargs):
            call_args_list.append(kwargs)
            return bad if len(call_args_list) == 1 else good

        with patch("app.services.llm.generate_text", new=_capture):
            await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        assert len(call_args_list) == 2
        retry_system = call_args_list[1]["system_prompt"]
        # Im Retry-System-Prompt steht der Issue-Typ + Vermeidungs-Hinweis
        assert "icd_halluzination" in retry_system
        assert "Vermeide" in retry_system

    @pytest.mark.asyncio
    async def test_faithful_summary_kein_retry_keine_issues(self):
        """Saubere Synthese ohne Hallu/Length-Issue → kein Retry, leere Issues."""
        raw = "[A]: Wie geht es?\n[B]: Mir geht es schlecht.\n" * 800
        good = _ok_result(words=1200, text=_faithful_summary(1200))
        mock = AsyncMock(return_value=good)

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        assert mock.call_count == 1
        assert result["retry_used"] is False
        assert result["degraded"] is False
        # Keine critical/high Issues
        criticals = [i for i in result["issues"] if i["severity"] == "critical"]
        highs = [i for i in result["issues"] if i["severity"] == "high"]
        assert criticals == []
        assert highs == []

    @pytest.mark.asyncio
    async def test_icd_in_source_keine_warnung(self):
        """Wenn ICD im Transkript explizit genannt wurde, KEIN Retry."""
        raw = (
            "[A]: Hatten Sie schon einmal die Diagnose F33.2 erhalten?\n"
            "[B]: Ja, vor zwei Jahren.\n"
        ) * 800  # ~6400 Woerter
        # Diese Summary erwaehnt F33.2 - aber das steht auch in der Quelle
        summary_with_legitimate_icd = _summary_with_invented_icd(1200)
        ok = _ok_result(words=1200, text=summary_with_legitimate_icd)
        mock = AsyncMock(return_value=ok)

        with patch("app.services.llm.generate_text", new=mock):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        # Kein Retry, weil ICD legitim aus Quelle stammt
        assert mock.call_count == 1
        assert result["retry_used"] is False
        # Issues sollten den ICD nicht beanstanden
        icd_issues = [
            i for i in result["issues"] if i["type"] == "icd_halluzination"
        ]
        assert icd_issues == []

    @pytest.mark.asyncio
    async def test_length_und_hallu_zusammen_triggern_einen_retry(self):
        """Zu kurz UND ICD erfunden → ein einziger Retry der beides addressiert."""
        raw = "[A]: Hallo.\n[B]: Hi.\n" * 800
        # Zu kurz UND mit erfundenem ICD: 50 Woerter Text
        bad = _ok_result(words=50, text="### 1. Test\nDiagnose F33.2 vorhanden. Sehr kurz.")
        good = _ok_result(words=1200, text=_faithful_summary(1200))

        call_args_list = []

        async def _capture(**kwargs):
            call_args_list.append(kwargs)
            return bad if len(call_args_list) == 1 else good

        with patch("app.services.llm.generate_text", new=_capture):
            result = await summarize_transcript(
                transcript_text=raw,
                workflow="dokumentation",
            )

        # Genau EIN Retry trotz zweier Problemtypen
        assert len(call_args_list) == 2
        assert result["retry_used"] is True
        assert result["degraded"] is False
        # Retry-User-Content muss MINDESTENS erwaehnen
        retry_user = call_args_list[1]["user_content"]
        assert "MINDESTENS" in retry_user
        # Und Retry-System-Prompt muss Hallu-Issue erwaehnen
        retry_system = call_args_list[1]["system_prompt"]
        assert "icd_halluzination" in retry_system
