"""
Pydantic-Schemas fuer Request- und Response-Validierung.
"""
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


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
    workflow: Literal["dokumentation", "anamnese", "verlaengerung", "entlassbericht"]
    prompt: str = Field(..., description="Angepasster System-Prompt")
    transcript: Optional[str] = Field(None, description="Transkripttext (optional)")
    bullets: Optional[str] = Field(None, description="Stichpunkte (optional, Workflow 1)")
    diagnosen: Optional[list[str]] = Field(None, description="ICD-Codes (Workflow 2)")
    style_context: Optional[str] = Field(None, description="Stilkontext aus Beispieltext")


class GenerateResponse(BaseModel):
    job_id: str
    text: str
    model_used: str
    duration_seconds: float
    token_count: Optional[int] = None


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
