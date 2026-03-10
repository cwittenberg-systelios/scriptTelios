"""
Tests fuer das sysTelios Backend.

Ausfuehren:  pytest -v
"""
import io
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Testumgebung konfigurieren bevor App importiert wird
import os
os.environ.update({
    "LLM_BACKEND":              "anthropic",
    "ANTHROPIC_API_KEY":        "test-key",
    "WHISPER_BACKEND":          "local",
    "DATABASE_URL":             "sqlite+aiosqlite:///./test.db",
    "SECRET_KEY":               "test-secret",
    "DELETE_AUDIO_AFTER_TRANSCRIPTION": "false",
    "UPLOAD_DIR":               "/tmp/systelios_test_uploads",
    "OUTPUT_DIR":               "/tmp/systelios_test_outputs",
})

from app.main import app  # noqa: E402

client = TestClient(app)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def mock_llm():
    """Ersetzt LLM-Aufruf durch einen fixen Beispieltext."""
    with patch(
        "app.services.llm.generate_text",
        new=AsyncMock(return_value={
            "text": "Dies ist eine generierte Verlaufsnotiz zu Testzwecken.",
            "model_used": "anthropic/claude-sonnet-4-20250514",
            "duration_s": 1.2,
            "token_count": 42,
        }),
    ):
        yield


@pytest.fixture
def mock_transcribe():
    """Ersetzt Whisper durch ein fixes Transkript."""
    with patch(
        "app.services.transcription.transcribe_audio",
        new=AsyncMock(return_value={
            "transcript": "Der Patient berichtet von verbesserten Schlafgewohnheiten.",
            "language": "de",
            "duration_s": 120.0,
            "word_count": 10,
        }),
    ):
        yield


@pytest.fixture
def mock_extract():
    """Ersetzt PDF-Extraktion durch Beispieltext."""
    with patch(
        "app.services.extraction.extract_text",
        new=AsyncMock(return_value="Selbstauskunft: Patient leidet unter Schlafproblemen."),
    ):
        yield


# ── Health ────────────────────────────────────────────────────────

def test_health():
    with patch("httpx.AsyncClient") as _:
        r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "llm_model" in data
    assert "whisper_model" in data


# ── Generate (einfach) ────────────────────────────────────────────

def test_generate_dokumentation(mock_llm):
    r = client.post("/api/generate", json={
        "workflow": "dokumentation",
        "prompt":   "Du bist ein Therapeut ...",
        "transcript": "Patient berichtet von Besserung.",
    })
    assert r.status_code == 200
    data = r.json()
    assert "text" in data
    assert "job_id" in data
    assert "model_used" in data
    assert len(data["text"]) > 0


def test_generate_anamnese_mit_diagnosen(mock_llm):
    r = client.post("/api/generate", json={
        "workflow":  "anamnese",
        "prompt":    "Du bist ein Arzt ...",
        "diagnosen": ["F32.1", "Z73.0"],
        "transcript": "Patient wurde aufgenommen mit depressiver Symptomatik.",
    })
    assert r.status_code == 200
    assert r.json()["text"]


def test_generate_ungueltiger_workflow(mock_llm):
    r = client.post("/api/generate", json={
        "workflow": "unbekannt",
        "prompt":   "test",
    })
    assert r.status_code == 422


# ── Generate mit Datei-Upload ─────────────────────────────────────

def test_generate_with_audio(mock_llm, mock_transcribe):
    audio_bytes = b"RIFF" + b"\x00" * 100   # Minimales WAV-Dummy
    r = client.post(
        "/api/generate/with-files",
        data={"workflow": "dokumentation", "prompt": "Du bist ein Therapeut ..."},
        files={"audio": ("test.wav", io.BytesIO(audio_bytes), "audio/wav")},
    )
    assert r.status_code == 200
    assert r.json()["text"]


def test_generate_with_selbstauskunft(mock_llm, mock_extract):
    pdf_bytes = b"%PDF-1.4 Fake PDF content"
    r = client.post(
        "/api/generate/with-files",
        data={
            "workflow":   "anamnese",
            "prompt":     "Du bist ein Arzt ...",
            "diagnosen":  "F32.1,F41.1",
        },
        files={"selbstauskunft": ("selbstauskunft.pdf", io.BytesIO(pdf_bytes), "application/pdf")},
    )
    assert r.status_code == 200


# ── Transkription ─────────────────────────────────────────────────

def test_transcribe_ungueltiges_format():
    r = client.post(
        "/api/transcribe",
        files={"file": ("dokument.pdf", io.BytesIO(b"PDF"), "application/pdf")},
    )
    assert r.status_code == 422


def test_transcribe_audio(mock_transcribe):
    audio_bytes = b"RIFF" + b"\x00" * 100
    r = client.post(
        "/api/transcribe",
        files={"file": ("aufnahme.wav", io.BytesIO(audio_bytes), "audio/wav")},
    )
    assert r.status_code == 200
    data = r.json()
    assert "transcript" in data
    assert data["word_count"] > 0
    assert data["language"] == "de"


# ── Download ──────────────────────────────────────────────────────

def test_download_nicht_vorhanden():
    r = client.get("/api/documents/download/nichtvorhanden.docx")
    assert r.status_code == 404


def test_download_path_traversal():
    r = client.get("/api/documents/download/../etc/passwd")
    assert r.status_code == 400


# ── Prompts ───────────────────────────────────────────────────────

def test_build_system_prompt():
    from app.services.prompts import build_system_prompt
    p = build_system_prompt(
        workflow="dokumentation",
        diagnosen=["F32.1"],
        style_context="Schreibe praegnant.",
    )
    assert "Schreibe praegnant" in p
    assert "sysTelios" in p


def test_build_user_content():
    from app.services.prompts import build_user_content
    u = build_user_content(
        workflow="dokumentation",
        transcript="Patient berichtet ...",
        bullets="- Schlafprobleme",
    )
    assert "TRANSKRIPT" in u
    assert "STICHPUNKTE" in u


def test_build_user_content_anamnese():
    from app.services.prompts import build_user_content
    u = build_user_content(
        workflow="anamnese",
        selbstauskunft_text="Selbstauskunft ...",
        diagnosen=["F32.1"],
    )
    assert "SELBSTAUSKUNFT" in u
    assert "F32.1" in u
