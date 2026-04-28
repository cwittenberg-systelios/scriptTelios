"""
Pydantic-Schemas fuer Request- und Response-Validierung.
"""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ── v16 Audit-Patch B2: Zentrale Workflow-Liste ───────────────────────────────
# Vorher 4x dupliziert (schemas.py, db.py, jobs.py:266, jobs.py:608).
# Jetzt zentral hier. Andere Module importieren WORKFLOW_NAMES und
# WorkflowLiteral von hier statt eigene Listen zu pflegen.
WORKFLOW_NAMES: list[str] = [
    "dokumentation",
    "anamnese",
    "verlaengerung",
    "folgeverlaengerung",
    "akutantrag",
    "entlassbericht",
]
WorkflowLiteral = Literal[
    "dokumentation", "anamnese", "verlaengerung",
    "folgeverlaengerung", "akutantrag", "entlassbericht",
]


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
    job_id: str
    status: Literal["pending", "running", "done", "error"]
    workflow: str
    step: str
    result_text: Optional[str] = None
    result_file: Optional[str] = None
    error_msg: Optional[str] = None
    created_at: datetime
    duration_seconds: Optional[int] = None


# ── Transkription ─────────────────────────────────────────────────
class TranscribeResponse(BaseModel):
    job_id: str
    transcript: str
    language: str
    duration_seconds: float
    word_count: int


# ── Generierung ───────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    workflow: WorkflowLiteral
    prompt: str = Field(..., description="Angepasster System-Prompt")
    therapeut_id: Optional[str] = Field(None, description="Therapeuten-ID fuer pgvector-Retrieval")
    transcript: Optional[str] = Field(None, description="Transkripttext (optional)")
    bullets: Optional[str] = Field(None, description="Stichpunkte (optional, Workflow 1)")
    diagnosen: Optional[list[str]] = Field(None, description="ICD-Codes (Workflow 2)")
    style_context: Optional[str] = Field(None, description="Direkter Stilkontext (ueberschreibt pgvector)")


class GenerateResponse(BaseModel):
    job_id: str
    text: str
    model_used: str
    duration_seconds: float
    token_count: Optional[int] = None


# ── OCR / Dokumentenextraktion ────────────────────────────────────
class ExtractionInfo(BaseModel):
    """Metadaten einer Dokumenten-Extraktion – fuer Transparenz im Frontend."""
    method: str          # pdfplumber | tesseract | ollama_vision | docx | txt | image_tess | image_vision
    quality: float       # 0.0-1.0 geschaetzte Qualitaet
    pages: int
    warnings: list[str] = []


class ExtractionResponse(BaseModel):
    """Response fuer POST /api/documents/extract (Vorschau ohne Generierung)."""
    filename: str
    text: str
    char_count: int
    word_count: int
    extraction: ExtractionInfo


# ── Dokument-Verarbeitung (Workflow 3 & 4) ────────────────────────
class DocProcessResponse(BaseModel):
    job_id: str
    download_url: str
    filename: str
    preview_text: str   # Erste 500 Zeichen als Vorschau


# ── Stilprofil ────────────────────────────────────────────────────
class StyleProfileResponse(BaseModel):
    profile_id: str
    therapeut_id: str
    style_context: str
    word_count: Optional[int] = None
    created_at: datetime


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
