"""
tests/unit/test_schemas_repair.py
─────────────────────────────────
Pydantic-Validierungs-Tests fuer die Repair-Schemas (v19 Phase C).

Schemas im Test:
  - RepairPreviewRequest
  - RepairRequest
  - QualityIssueResponse
  - RepairPreviewResponse
  - RepairResponse

Keine DB, kein LLM. Reine Schema-Validierung.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.schemas import (
    QualityIssueResponse,
    RepairPreviewRequest,
    RepairPreviewResponse,
    RepairRequest,
    RepairResponse,
)


# ── RepairPreviewRequest ──────────────────────────────────────────────────────

class TestRepairPreviewRequest:

    def test_defaults(self):
        req = RepairPreviewRequest()
        assert req.accepted_issue_codes == []
        assert req.user_hint == ""

    def test_valid_codes(self):
        req = RepairPreviewRequest(
            accepted_issue_codes=["LENGTH_TOO_SHORT", "MISSING_KEYWORD_BIOGRAFIE"],
            user_hint="Bitte empathischer.",
        )
        assert len(req.accepted_issue_codes) == 2

    def test_invalid_code_lowercase_rejected(self):
        with pytest.raises(ValidationError) as exc:
            RepairPreviewRequest(accepted_issue_codes=["length_too_short"])
        # Fehler-Message enthaelt den ungueltigen Code
        assert "length_too_short" in str(exc.value)

    def test_invalid_code_with_digit_rejected(self):
        with pytest.raises(ValidationError):
            RepairPreviewRequest(accepted_issue_codes=["LENGTH_123"])

    def test_invalid_code_with_dash_rejected(self):
        with pytest.raises(ValidationError):
            RepairPreviewRequest(accepted_issue_codes=["LENGTH-TOO-SHORT"])

    def test_invalid_code_with_space_rejected(self):
        with pytest.raises(ValidationError):
            RepairPreviewRequest(accepted_issue_codes=["LENGTH TOO SHORT"])

    def test_too_many_codes_rejected(self):
        # max_length=20
        codes = [f"CODE_{i}" for i in range(21)]
        with pytest.raises(ValidationError):
            RepairPreviewRequest(accepted_issue_codes=codes)

    def test_user_hint_too_long_rejected(self):
        # max_length=500
        with pytest.raises(ValidationError):
            RepairPreviewRequest(user_hint="x" * 501)

    def test_user_hint_with_nul_byte_rejected(self):
        with pytest.raises(ValidationError):
            RepairPreviewRequest(user_hint="hello\x00world")

    def test_user_hint_with_ansi_escape_rejected(self):
        with pytest.raises(ValidationError):
            RepairPreviewRequest(user_hint="hello\x1b[31mred\x1b[0m")

    def test_user_hint_keeps_newlines_and_tabs(self):
        req = RepairPreviewRequest(user_hint="Zeile1\nZeile2\tindent")
        assert "\n" in req.user_hint
        assert "\t" in req.user_hint

    def test_user_hint_stripped(self):
        req = RepairPreviewRequest(user_hint="   text   ")
        assert req.user_hint == "text"


# ── RepairRequest ─────────────────────────────────────────────────────────────

class TestRepairRequest:

    def test_defaults(self):
        req = RepairRequest()
        assert req.accepted_issue_codes == []
        assert req.user_hint == ""
        assert req.custom_final_prompt is None

    def test_with_custom_prompt(self):
        req = RepairRequest(
            accepted_issue_codes=["LENGTH_TOO_SHORT"],
            user_hint="ok",
            custom_final_prompt="Hier mein eigener Prompt.",
        )
        assert req.custom_final_prompt == "Hier mein eigener Prompt."

    def test_custom_prompt_max_length(self):
        # max_length=8000
        with pytest.raises(ValidationError):
            RepairRequest(custom_final_prompt="x" * 8001)

    def test_invalid_codes_rejected(self):
        with pytest.raises(ValidationError):
            RepairRequest(accepted_issue_codes=["bad-code"])


# ── QualityIssueResponse ──────────────────────────────────────────────────────

class TestQualityIssueResponse:

    def test_valid(self):
        r = QualityIssueResponse(
            code="LENGTH_TOO_SHORT",
            severity="warning",
            message="zu kurz",
            repair_hint="erweitern",
        )
        assert r.code == "LENGTH_TOO_SHORT"
        assert r.code_detail == {}

    def test_invalid_severity_rejected(self):
        with pytest.raises(ValidationError):
            QualityIssueResponse(
                code="X", severity="urgent",
                message="m", repair_hint="r",
            )

    def test_severity_literals(self):
        for sev in ("critical", "warning", "info"):
            r = QualityIssueResponse(code="X", severity=sev, message="m")
            assert r.severity == sev


# ── RepairPreviewResponse + RepairResponse ─────────────────────────────────────

class TestRepairResponses:

    def test_preview_response(self):
        resp = RepairPreviewResponse(
            final_prompt="Bitte ueberarbeite...",
            accepted_issues=[
                QualityIssueResponse(
                    code="LENGTH_TOO_SHORT", severity="warning",
                    message="zu kurz", repair_hint="erweitern",
                ),
            ],
            user_hint_sanitized="Bitte empathisch.",
        )
        assert resp.final_prompt.startswith("Bitte ueberarbeite")
        assert len(resp.accepted_issues) == 1

    def test_repair_response(self):
        resp = RepairResponse(
            repair_job_id="abc123",
            parent_job_id="xyz789",
            workflow="anamnese",
        )
        assert resp.repair_job_id == "abc123"

    def test_repair_response_serializable(self):
        # Sicherstellen dass Response zu JSON konvertibel ist (FastAPI-Pfad).
        resp = RepairResponse(
            repair_job_id="a", parent_job_id="b", workflow="anamnese",
        )
        d = resp.model_dump()
        assert d == {"repair_job_id": "a", "parent_job_id": "b", "workflow": "anamnese"}
