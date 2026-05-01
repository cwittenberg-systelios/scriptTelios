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
from pydantic import BaseModel

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
