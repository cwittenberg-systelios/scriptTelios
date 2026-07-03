"""
tests/unit/test_jobs_stage1_phases.py
─────────────────────────────────────
R2 (2026-07-01): Die Stage-1-Bloecke wurden aus der _run()-Closure in
Modul-Funktionen extrahiert (_run_verlauf_stage1, _run_transcript_stage1).
Diese Tests frieren das Verhalten ein: Erfolg ersetzt den Text + Audit,
Exception faellt aufs Original zurueck + Audit mit fallback_reason,
Nicht-Whitelist-Workflow liefert (Original, None).

Kein LLM, keine DB - summarize_* wird gemockt.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


def _job():
    j = MagicMock()
    j.job_id = "test123"
    j.set_progress = MagicMock()
    return j


_SUMMARY_RESULT = {
    "summary": "Verdichteter Text.",
    "raw_word_count": 2000,
    "summary_word_count": 400,
    "compression_ratio": 0.2,
    "duration_s": 12.5,
    "telemetry": {"think_ratio": 0.0},
    "retry_used": False,
    "retry_telemetry": {},
    "degraded": False,
    "issues": [],
    "target_words": 800,
}


class TestRunVerlaufStage1:

    @pytest.mark.asyncio
    async def test_erfolg_ersetzt_text_und_baut_audit(self):
        from app.api.jobs import _run_verlauf_stage1

        async def _fake(**kwargs):
            return dict(_SUMMARY_RESULT)

        long_text = "Wort " * 2000  # > 1500-Woerter-Schwelle
        with patch("app.api.jobs.summarize_verlauf", _fake):
            text, audit = await _run_verlauf_stage1(
                workflow="verlaengerung",
                verlaufsdoku_text=long_text,
                patient_initial="Herr Z.",
                job=_job(),
                bands={},
            )
        assert text == "Verdichteter Text."
        assert audit["applied"] is True
        assert audit["raw_word_count"] == 2000
        assert audit["fallback_reason"] is None

    @pytest.mark.asyncio
    async def test_exception_faellt_auf_original_zurueck(self):
        from app.api.jobs import _run_verlauf_stage1

        async def _boom(**kwargs):
            raise RuntimeError("Ollama down")

        long_text = "Wort " * 2000
        with patch("app.api.jobs.summarize_verlauf", _boom):
            text, audit = await _run_verlauf_stage1(
                workflow="verlaengerung",
                verlaufsdoku_text=long_text,
                patient_initial=None,
                job=_job(),
                bands={},
            )
        assert text == long_text  # Original unveraendert
        assert audit["applied"] is False
        assert "RuntimeError" in audit["fallback_reason"]

    @pytest.mark.asyncio
    async def test_fremder_workflow_liefert_original_ohne_audit(self):
        from app.api.jobs import _run_verlauf_stage1
        text, audit = await _run_verlauf_stage1(
            workflow="dokumentation",   # nicht in Verlauf-Whitelist
            verlaufsdoku_text="kurz",
            patient_initial=None,
            job=_job(),
            bands={},
        )
        assert text == "kurz"
        assert audit is None


class TestRunTranscriptStage1:

    @pytest.mark.asyncio
    async def test_erfolg_ersetzt_text_und_persistiert_summary(self):
        from app.api.jobs import _run_transcript_stage1

        async def _fake(**kwargs):
            return dict(_SUMMARY_RESULT)

        long_tr = "Wort " * 3000  # > 2800-Schwelle
        with patch("app.api.jobs.summarize_transcript", _fake):
            text, summary, audit = await _run_transcript_stage1(
                workflow="dokumentation",
                transkript_text=long_tr,
                patient_initial="Frau M.",
                job=_job(),
                bands={},
            )
        assert text == "Verdichteter Text."
        assert summary == "Verdichteter Text."  # v19.3 Repair-Persistierung
        assert audit["applied"] is True

    @pytest.mark.asyncio
    async def test_exception_behaelt_rohtranskript(self):
        from app.api.jobs import _run_transcript_stage1

        async def _boom(**kwargs):
            raise ValueError("kaputt")

        long_tr = "Wort " * 3000
        with patch("app.api.jobs.summarize_transcript", _boom):
            text, summary, audit = await _run_transcript_stage1(
                workflow="dokumentation",
                transkript_text=long_tr,
                patient_initial=None,
                job=_job(),
                bands={},
            )
        assert text == long_tr
        assert summary is None
        assert audit["applied"] is False
        assert "ValueError" in audit["fallback_reason"]

    @pytest.mark.asyncio
    async def test_kurzes_transkript_skip_mit_audit_grund(self):
        from app.api.jobs import _run_transcript_stage1
        short = "Wort " * 100  # unter 2800
        text, summary, audit = await _run_transcript_stage1(
            workflow="dokumentation",
            transkript_text=short,
            patient_initial=None,
            job=_job(),
            bands={},
        )
        assert text == short
        assert summary is None
        assert audit is not None and audit["applied"] is False
        assert audit["fallback_reason"]  # Skip-Grund dokumentiert
