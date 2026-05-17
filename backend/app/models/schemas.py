"""
Pydantic-Schemas fuer Request- und Response-Validierung.

Hinweis: Frueher enthielt diese Datei auch Modelle fuer die Endpunkte
/api/generate(/with-files), /api/transcribe und /api/documents/* . Diese
Endpunkte wurden entfernt – das Frontend nutzt ausschliesslich
/api/jobs/generate, was Dicts statt Pydantic-Modelle zurueckgibt.

Verbliebene Modelle:
  - HealthResponse        (von /api/health verwendet)
  - JobStatus             (Pydantic-Modell, nicht zu verwechseln mit
                           app.services.job_queue.JobStatus, dem Enum;
                           Modell wird derzeit nicht aktiv referenziert,
                           bleibt fuer eventuelle Reaktivierung)
  - StyleEmbedding*       (von /api/style/upload und /api/style/{tid})

Re-Exports:
  - WorkflowLiteral       (aus app.core.workflows; behaelt den bisherigen
                           Import-Pfad fuer jobs.py & Co. bei)
"""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator

# v13: WorkflowLiteral kommt jetzt aus dem zentralen Workflow-Modul.
# Wir re-exportieren ihn hier, damit der bisherige Import-Pfad
# `from app.models.schemas import WorkflowLiteral` weiter funktioniert.
from app.core.workflows import WorkflowLiteral  # noqa: F401  (re-export)


# ── Health ────────────────────────────────────────────────────────
class HealthResponse(BaseModel):
    status: str
    llm_backend: str
    llm_model: str
    whisper_backend: str
    whisper_model: str
    version: str = "1.0.0"


# ── Jobs ──────────────────────────────────────────────────────────
class JobStatus(BaseModel):
    """Pydantic-Response-Modell. Nicht zu verwechseln mit
    app.services.job_queue.JobStatus (str-Enum)."""
    job_id: str
    status: Literal["pending", "running", "done", "error"]
    workflow: str
    step: str
    result_text: Optional[str] = None
    result_file: Optional[str] = None
    error_msg: Optional[str] = None
    created_at: datetime
    duration_seconds: Optional[int] = None


# ── Stil-Embedding (pgvector) ─────────────────────────────────────
class StyleEmbeddingUploadResponse(BaseModel):
    embedding_id: str
    therapeut_id: str
    dokumenttyp: str
    dokumenttyp_label: str
    word_count: int
    ist_statisch: bool
    created_at: datetime


class StyleEmbeddingInfo(BaseModel):
    embedding_id: str
    dokumenttyp: str
    dokumenttyp_label: str
    word_count: Optional[int]
    ist_statisch: bool
    created_at: datetime
    # Vorschau der ersten 200 Zeichen
    text_preview: str


class StyleEmbeddingListResponse(BaseModel):
    therapeut_id: str
    total: int
    embeddings: list[StyleEmbeddingInfo]


# ── Repair (v19 Phase C) ──────────────────────────────────────────
#
# Drei Schemas:
#   RepairPreviewRequest  - was das UI sendet, um den final_prompt zu bauen
#   RepairPreviewResponse - der gebaute final_prompt + akzeptierte Issues
#   RepairRequest         - Trigger fuer den Repair-Job; kann custom_final_prompt
#                           setzen falls der Therapeut den Preview editiert hat.
#
# Validierungs-Pflichten (Plan-Anforderung):
#   - user_hint:            max 500 Zeichen, keine Control-Chars ausser \n
#   - accepted_issue_codes: max 20 Codes, jeder matched ^[A-Z_]+$
#   - custom_final_prompt:  max 8000 Zeichen

_ISSUE_CODE_RE = __import__("re").compile(r"^[A-Z_]+$")
# Erlaubte Whitespace-Klassen im user_hint: nur Tab/Newline/Space.
# Alle anderen Control-Chars werden rejected (Defense gegen Prompt-Injection
# via U+200B Zero-Width-Spaces, NUL-Bytes, ANSI-Escape-Sequenzen etc.).
_USER_HINT_CONTROL_RE = __import__("re").compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def _validate_issue_codes(codes: list[str]) -> list[str]:
    """Codes muessen ^[A-Z_]+$ matchen. Wirft ValueError mit Liste aller
    invaliden Codes - damit das UI eine praezise Fehlermeldung zeigen kann."""
    bad = [c for c in codes if not _ISSUE_CODE_RE.match(c or "")]
    if bad:
        raise ValueError(
            f"Issue-Codes muessen ^[A-Z_]+$ matchen, ungueltig: {bad}"
        )
    return codes


def _sanitize_user_hint(hint: str) -> str:
    """Strippt Whitespace + entfernt Control-Chars (ausser \\n, \\t).

    Tatsaechliche Marker-Strip (>>>, [INST], <|im_start|> etc.) passiert
    spaeter in quality_check.build_repair_prompt() - dort liegt die
    Anti-Injection-Verantwortung. Hier nur Basis-Saeuberung."""
    if not hint:
        return ""
    stripped = hint.strip()
    if _USER_HINT_CONTROL_RE.search(stripped):
        raise ValueError("user_hint enthaelt unerlaubte Steuerzeichen")
    return stripped


class RepairPreviewRequest(BaseModel):
    """POST /api/jobs/{job_id}/repair/preview - Body."""
    accepted_issue_codes: list[str] = Field(default_factory=list, max_length=20)
    user_hint:            str       = Field(default="", max_length=500)

    @field_validator("accepted_issue_codes")
    @classmethod
    def _check_codes(cls, v: list[str]) -> list[str]:
        return _validate_issue_codes(v)

    @field_validator("user_hint")
    @classmethod
    def _check_hint(cls, v: str) -> str:
        return _sanitize_user_hint(v)


class QualityIssueResponse(BaseModel):
    """Issue-Form wie im RepairPreviewResponse zurueckgegeben."""
    code:        str
    severity:    Literal["critical", "warning", "info"]
    message:     str
    repair_hint: str = ""
    code_detail: dict = Field(default_factory=dict)


class RepairPreviewResponse(BaseModel):
    """POST /api/jobs/{job_id}/repair/preview - Response."""
    final_prompt:       str
    accepted_issues:    list[QualityIssueResponse]
    user_hint_sanitized: str


class RepairRequest(BaseModel):
    """POST /api/jobs/{job_id}/repair - Body.

    Wenn custom_final_prompt gesetzt:
      Der Therapeut hat den Preview-Prompt im UI manuell editiert. Backend
      verwendet exakt diesen Prompt statt selbst neu zu bauen. accepted_issue_codes
      + user_hint werden trotzdem mitgesendet (fuer Audit-Log).

    Wenn custom_final_prompt NULL:
      Backend baut den Prompt frisch aus issue_codes + user_hint zusammen
      (identisch zur Preview).
    """
    accepted_issue_codes: list[str] = Field(default_factory=list, max_length=20)
    user_hint:            str       = Field(default="", max_length=500)
    custom_final_prompt:  Optional[str] = Field(default=None, max_length=8000)

    @field_validator("accepted_issue_codes")
    @classmethod
    def _check_codes(cls, v: list[str]) -> list[str]:
        return _validate_issue_codes(v)

    @field_validator("user_hint")
    @classmethod
    def _check_hint(cls, v: str) -> str:
        return _sanitize_user_hint(v)


class RepairResponse(BaseModel):
    """POST /api/jobs/{job_id}/repair - Response (synchron, Job ist async)."""
    repair_job_id:  str
    parent_job_id:  str
    workflow:       str
