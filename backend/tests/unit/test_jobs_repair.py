"""
tests/unit/test_jobs_repair.py
──────────────────────────────
Tests fuer die Phase-C-Repair-Endpoints und Helpers (v19 Phase C).

Strategie:
  - Helper (`_split_anamnese_concat`, `_build_repair_context`) werden direkt
    aufgerufen, kein TestClient noetig.
  - Endpoints (`repair_preview`, `repair_execute`) werden ebenfalls direkt
    aufgerufen mit Monkeypatch fuer `_resolve_parent_job` und Stub fuer
    `job_queue.create_repair_job` / `run_job`.
  - Kein LLM, keine DB - reine Logik.

Pflicht: Laufzeit < 5 Sekunden.
"""
from __future__ import annotations

import asyncio
import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.jobs import (
    _build_repair_context,
    _resolve_parent_job,
    _split_anamnese_concat,
    repair_execute,
    repair_preview,
)
from app.models.schemas import RepairPreviewRequest, RepairRequest
from app.services.quality_check import (
    QualityIssue,
    SEVERITY_WARNING,
    serialize_issues,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _qc_dict(issues: list[QualityIssue], workflow: str = "anamnese") -> dict:
    return serialize_issues(issues, workflow=workflow)


def _parent_done(
    workflow: str = "anamnese",
    result_text: str = "Original text content.",
    befund_text: str | None = None,
    quality_check: dict | None = None,
) -> dict:
    """Baut ein parent-Job-dict wie es from_db zurueckgeben wuerde."""
    return {
        "job_id": "parent-1",
        "workflow": workflow,
        "status": "done",
        "result_text": result_text,
        "befund_text": befund_text or "",
        "quality_check": quality_check,
        "model_used": "qwen3:32b",
    }


# ── _split_anamnese_concat ────────────────────────────────────────────────────

class TestSplitAnamneseConcat:

    def test_anamnese_with_separator(self):
        text = "AnamneseTeil.\n\n###BEFUND###\n\nBefundTeil."
        a, b = _split_anamnese_concat("anamnese", text)
        assert a == "AnamneseTeil."
        assert b == "BefundTeil."

    def test_anamnese_without_separator(self):
        a, b = _split_anamnese_concat("anamnese", "Nur Anamnese.")
        assert a == "Nur Anamnese."
        assert b is None

    def test_non_anamnese_no_split(self):
        # Auch wenn ###BEFUND### im Text steht: kein Split
        text = "Verlauf\n\n###BEFUND###\n\nIrrelevant."
        a, b = _split_anamnese_concat("verlaengerung", text)
        assert a == text
        assert b is None

    def test_empty_text(self):
        a, b = _split_anamnese_concat("anamnese", "")
        assert a == ""
        assert b is None


# ── _build_repair_context ─────────────────────────────────────────────────────

class TestBuildRepairContext:

    def test_happy_path(self):
        issues = [QualityIssue(
            "LENGTH_TOO_SHORT", SEVERITY_WARNING,
            "zu kurz", "erweitern",
        )]
        parent = _parent_done(quality_check=_qc_dict(issues))
        wf, original, accepted, prompt = _build_repair_context(
            parent, ["LENGTH_TOO_SHORT"], "Bitte besser.",
        )
        assert wf == "anamnese"
        assert len(accepted) == 1
        assert accepted[0].code == "LENGTH_TOO_SHORT"
        assert "LENGTH_TOO_SHORT" in prompt
        assert "Bitte besser." in prompt

    def test_rejects_non_done(self):
        parent = _parent_done()
        parent["status"] = "running"
        with pytest.raises(HTTPException) as exc:
            _build_repair_context(parent, [], "")
        assert exc.value.status_code == 400

    def test_rejects_empty_result_text(self):
        parent = _parent_done(result_text="   ")
        with pytest.raises(HTTPException) as exc:
            _build_repair_context(parent, [], "")
        assert exc.value.status_code == 400

    def test_rejects_unknown_code(self):
        issues = [QualityIssue(
            "LENGTH_TOO_SHORT", SEVERITY_WARNING, "x", "y",
        )]
        parent = _parent_done(quality_check=_qc_dict(issues))
        with pytest.raises(HTTPException) as exc:
            _build_repair_context(parent, ["NEVER_HEARD_OF"], "")
        assert exc.value.status_code == 422
        # Detail enthaelt die unknowns
        detail = exc.value.detail
        assert isinstance(detail, dict)
        assert "NEVER_HEARD_OF" in detail["unknown_codes"]

    def test_anamnese_concatenates_befund(self):
        # Anamnese-Two-Stage: original_text im Prompt enthaelt Befund + Trenner.
        issues = [QualityIssue(
            "MISSING_KEYWORD_BEHANDLUNGSVERLAUF",
            SEVERITY_WARNING, "fehlt", "ergaenzen",
        )]
        parent = _parent_done(
            workflow="anamnese",
            result_text="Anamnese-Teil.",
            befund_text="Befund-Teil.",
            quality_check=_qc_dict(issues),
        )
        wf, original, accepted, prompt = _build_repair_context(
            parent, ["MISSING_KEYWORD_BEHANDLUNGSVERLAUF"], "",
        )
        # Im Prompt: beide Teile + Trenner
        assert "Anamnese-Teil." in prompt
        assert "###BEFUND###" in prompt
        assert "Befund-Teil." in prompt

    def test_empty_accepted_codes_still_works(self):
        # 0 Issues akzeptiert + leerer Hint -> baut trotzdem Prompt
        # (preview-Anwendungsfall: nur Hint, keine Issue-Auswahl)
        parent = _parent_done(quality_check=_qc_dict([]))
        wf, original, accepted, prompt = _build_repair_context(
            parent, [], "Bitte stilistisch verbessern.",
        )
        assert accepted == []
        assert "Bitte stilistisch verbessern." in prompt
        assert "keine spezifischen Issues" in prompt

    def test_missing_qc_treated_as_empty(self):
        # Pre-v19-Job: quality_check fehlt komplett.
        # Mit accepted_codes=[] und hint != "" sollte das funktionieren.
        parent = _parent_done(quality_check=None)
        wf, original, accepted, prompt = _build_repair_context(
            parent, [], "Hint nur.",
        )
        assert accepted == []
        assert "Hint nur." in prompt

    def test_missing_qc_with_unknown_code_rejected(self):
        # Pre-v19-Job + Code-Auswahl: muss als unknown rejected werden
        parent = _parent_done(quality_check=None)
        with pytest.raises(HTTPException) as exc:
            _build_repair_context(parent, ["ANY_CODE"], "")
        assert exc.value.status_code == 422


# ── repair_preview Endpoint ───────────────────────────────────────────────────

class TestRepairPreviewEndpoint:

    @pytest.mark.asyncio
    async def test_preview_happy_path(self):
        issues = [QualityIssue(
            "LENGTH_TOO_SHORT", SEVERITY_WARNING,
            "zu kurz", "erweitern",
        )]
        parent = _parent_done(quality_check=_qc_dict(issues))

        async def _resolve_mock(_job_id):
            return parent

        with patch("app.api.jobs._resolve_parent_job", _resolve_mock):
            req = RepairPreviewRequest(
                accepted_issue_codes=["LENGTH_TOO_SHORT"],
                user_hint="Test hint.",
            )
            resp = await repair_preview("parent-1", req, current_user="alice")

        assert "LENGTH_TOO_SHORT" in resp.final_prompt
        assert "Test hint." in resp.final_prompt
        assert len(resp.accepted_issues) == 1
        assert resp.accepted_issues[0].code == "LENGTH_TOO_SHORT"
        assert resp.user_hint_sanitized == "Test hint."

    @pytest.mark.asyncio
    async def test_preview_404_when_parent_missing(self):
        async def _resolve_mock(_job_id):
            raise HTTPException(status_code=404, detail="not found")

        with patch("app.api.jobs._resolve_parent_job", _resolve_mock):
            req = RepairPreviewRequest()
            with pytest.raises(HTTPException) as exc:
                await repair_preview("missing", req, current_user="alice")
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_preview_400_when_parent_not_done(self):
        parent = _parent_done()
        parent["status"] = "error"

        async def _resolve_mock(_job_id):
            return parent

        with patch("app.api.jobs._resolve_parent_job", _resolve_mock):
            req = RepairPreviewRequest()
            with pytest.raises(HTTPException) as exc:
                await repair_preview("p", req, current_user="alice")
            assert exc.value.status_code == 400


# ── repair_execute Endpoint ───────────────────────────────────────────────────

class TestRepairExecuteEndpoint:

    @pytest.mark.asyncio
    async def test_execute_creates_repair_job(self):
        issues = [QualityIssue(
            "LENGTH_TOO_SHORT", SEVERITY_WARNING, "x", "y",
        )]
        parent = _parent_done(quality_check=_qc_dict(issues))

        async def _resolve_mock(_job_id):
            return parent

        # job_queue.create_repair_job zurueckgeben ein Fake-Job
        fake_job = MagicMock()
        fake_job.job_id = "repair-abc"

        bg = MagicMock()
        bg.add_task = MagicMock()

        with patch("app.api.jobs._resolve_parent_job", _resolve_mock), \
             patch("app.api.jobs.job_queue.create_repair_job",
                   return_value=fake_job) as m_create:
            req = RepairRequest(
                accepted_issue_codes=["LENGTH_TOO_SHORT"],
                user_hint="Bitte.",
            )
            resp = await repair_execute(
                "parent-1", req, background_tasks=bg, current_user="alice",
            )

        # Antwort
        assert resp.repair_job_id == "repair-abc"
        assert resp.parent_job_id == "parent-1"
        assert resp.workflow == "anamnese"

        # create_repair_job wurde mit parent_job_id + repair_input aufgerufen
        m_create.assert_called_once()
        kwargs = m_create.call_args.kwargs
        assert kwargs["parent_job_id"] == "parent-1"
        assert kwargs["workflow"] == "anamnese"
        repair_input = kwargs["repair_input"]
        assert repair_input["accepted_issue_codes"] == ["LENGTH_TOO_SHORT"]
        assert repair_input["custom_final_prompt_used"] is False
        assert "LENGTH_TOO_SHORT" in repair_input["final_prompt"]

        # Background-Task wurde gequeued
        bg.add_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_with_custom_prompt(self):
        parent = _parent_done(quality_check=_qc_dict([]))

        async def _resolve_mock(_job_id):
            return parent

        fake_job = MagicMock()
        fake_job.job_id = "repair-custom"
        bg = MagicMock()
        bg.add_task = MagicMock()

        with patch("app.api.jobs._resolve_parent_job", _resolve_mock), \
             patch("app.api.jobs.job_queue.create_repair_job",
                   return_value=fake_job) as m_create:
            req = RepairRequest(
                custom_final_prompt="EXAKT DIESEN PROMPT VERWENDEN.",
            )
            resp = await repair_execute(
                "parent-1", req, background_tasks=bg, current_user="alice",
            )

        # custom_used = True
        repair_input = m_create.call_args.kwargs["repair_input"]
        assert repair_input["custom_final_prompt_used"] is True
        assert repair_input["final_prompt"] == "EXAKT DIESEN PROMPT VERWENDEN."

    @pytest.mark.asyncio
    async def test_execute_custom_prompt_rejects_non_done_parent(self):
        parent = _parent_done()
        parent["status"] = "running"

        async def _resolve_mock(_job_id):
            return parent

        bg = MagicMock()
        bg.add_task = MagicMock()

        with patch("app.api.jobs._resolve_parent_job", _resolve_mock):
            req = RepairRequest(custom_final_prompt="my prompt")
            with pytest.raises(HTTPException) as exc:
                await repair_execute(
                    "p", req, background_tasks=bg, current_user="alice",
                )
            assert exc.value.status_code == 400


# ── _run_repair_coroutine ─────────────────────────────────────────────────────

class TestRunRepairCoroutine:

    @pytest.mark.asyncio
    async def test_anamnese_splits_output_at_separator(self):
        from app.api.jobs import _run_repair_coroutine

        fake_job = MagicMock()
        fake_job.set_progress = MagicMock()

        mock_text = "Anamnese-Teil neu.\n\n###BEFUND###\n\nBefund-Teil neu."

        async def _fake_generate(*args, **kwargs):
            # Progress-Callback testen
            cb = kwargs.get("on_progress")
            if cb:
                cb({"ratio": 0.5, "count": 100})
            return {
                "text": mock_text,
                "model_used": "qwen3:32b",
                "telemetry": {"tok_count": 100},
                "retry_used": False,
                "degraded": False,
            }

        with patch("app.api.jobs.generate_text", _fake_generate):
            result = await _run_repair_coroutine(
                fake_job, "anamnese", "irrelevant prompt",
            )

        # Anamnese: text = nur Anamnese-Teil, befund_text = Befund-Teil
        assert result["text"] == "Anamnese-Teil neu."
        assert result["befund_text"] == "Befund-Teil neu."
        assert result["akut_text"] is None
        assert result["verlauf_summary_audit"] is None
        # Repair-Marker im Telemetry
        assert result["generation_telemetry"]["repair_run"] is True

        # Progress wurde aufgerufen
        assert fake_job.set_progress.call_count >= 1

    @pytest.mark.asyncio
    async def test_non_anamnese_no_split(self):
        from app.api.jobs import _run_repair_coroutine

        fake_job = MagicMock()
        fake_job.set_progress = MagicMock()

        async def _fake_generate(*args, **kwargs):
            return {
                "text": "Verlauf-Output mit ###BEFUND### Token mittendrin.",
                "model_used": "qwen3:32b",
                "telemetry": {},
                "retry_used": False,
                "degraded": False,
            }

        with patch("app.api.jobs.generate_text", _fake_generate):
            result = await _run_repair_coroutine(
                fake_job, "verlaengerung", "irrelevant",
            )

        # Kein Split fuer verlaengerung
        assert result["befund_text"] is None
        assert "###BEFUND###" in result["text"]


# ── job_queue.create_repair_job ───────────────────────────────────────────────

class TestCreateRepairJob:

    def test_create_repair_job_sets_parent_and_input(self):
        from app.services.job_queue import job_queue

        state = job_queue.create_repair_job(
            parent_job_id="parent-xyz",
            workflow="anamnese",
            description="test",
            repair_input={"accepted_issue_codes": ["X"], "user_hint": "h"},
        )
        try:
            assert state.parent_job_id == "parent-xyz"
            assert state.repair_input["accepted_issue_codes"] == ["X"]
            assert state.workflow == "anamnese"
            # to_dict liefert die Felder mit
            d = state.to_dict()
            assert d["parent_job_id"] == "parent-xyz"
            assert d["repair_input"]["accepted_issue_codes"] == ["X"]
        finally:
            # Cleanup damit Cache nicht waechst zwischen Tests
            job_queue._cache.pop(state.job_id, None)
