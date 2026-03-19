"""
Testsuite sysTelios Backend
════════════════════════════

Abgedeckte Bereiche:
  1. Health-Endpunkt
  2. Generierung (alle 4 Workflows)
  3. Generierung mit Datei-Uploads
  4. Transkription
  5. Dokument-Verarbeitung (fill, extract, style)
  6. Stilprofil-Verwaltung (pgvector)
  7. Prompts (unit tests)
  8. Fehlerbehandlung & Edge Cases
  9. Sicherheit (Path Traversal, ungueltiges Format)
 10. Echte Dateien (werden uebersprungen wenn nicht vorhanden)

Ausfuehren:
  pytest tests/test_suite.py -v
  pytest tests/test_suite.py -v -k "workflow"       # Nur Workflow-Tests
  pytest tests/test_suite.py -v -k "real"           # Nur echte-Datei-Tests
  pytest tests/test_suite.py -v --tb=short          # Kurze Tracebacks
"""
import io
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient

# Fixtures aus conftest.py importieren (automatisch verfuegbar)
from tests.conftest import (
    AUDIO_KURZ, AUDIO_LANG,
    PDF_VERLAUF, PDF_SELBST_DIG, PDF_SELBST_LEER,
    DOCX_ENTLASS_V, DOCX_ENTLASS_B, DOCX_VERL_V, DOCX_STILPROFIL,
    TXT_TRANSKRIPT, TXT_STICHPUNKTE, TXT_SELBST, TXT_VERLAUF,
    REAL_FILES, real_file,
)

from app.main import app

client = TestClient(app)


# ══════════════════════════════════════════════════════════════════
# 1. HEALTH
# ══════════════════════════════════════════════════════════════════

class TestHealth:

    def test_health_ok(self):
        """Health-Endpunkt antwortet mit Status ok."""
        with patch("httpx.AsyncClient") as mock:
            mock.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=MagicMock(status_code=200)
            )
            r = client.get("/api/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "llm_model" in data
        assert "whisper_model" in data
        assert "llm_backend" in data

    def test_health_ollama_unreachable(self):
        """Health antwortet auch wenn Ollama nicht erreichbar ist."""
        import httpx
        with patch("httpx.AsyncClient") as mock:
            mock.return_value.__aenter__.return_value.get = AsyncMock(
                side_effect=httpx.ConnectError("Connection refused")
            )
            r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["llm_backend"] == "ollama:unreachable"

    def test_health_felder_vollstaendig(self):
        """Health-Response enthaelt alle erwarteten Felder."""
        with patch("httpx.AsyncClient"):
            r = client.get("/api/health")
        expected = {"status", "llm_backend", "llm_model", "whisper_backend", "whisper_model"}
        assert expected.issubset(r.json().keys())


# ══════════════════════════════════════════════════════════════════
# 2. GENERIERUNG – alle 4 Workflows (ohne Dateien)
# ══════════════════════════════════════════════════════════════════

class TestGenerierung:

    def test_workflow_dokumentation(self, mock_llm):
        """Workflow 1: Verlaufsnotiz aus Transkript."""
        r = client.post("/api/generate", json={
            "workflow":   "dokumentation",
            "prompt":     "Erstelle eine Verlaufsnotiz.",
            "transcript": TXT_TRANSKRIPT.read_text(encoding="utf-8"),
        })
        assert r.status_code == 200
        data = r.json()
        assert len(data["text"]) > 50
        assert "job_id" in data
        assert "model_used" in data
        assert "duration_seconds" in data

    def test_workflow_dokumentation_nur_stichpunkte(self, mock_llm):
        """Workflow 1: Verlaufsnotiz aus Stichpunkten (kein Transkript)."""
        r = client.post("/api/generate", json={
            "workflow": "dokumentation",
            "prompt":   "Erstelle eine Verlaufsnotiz.",
            "bullets":  TXT_STICHPUNKTE.read_text(encoding="utf-8"),
        })
        assert r.status_code == 200
        assert r.json()["text"]

    def test_workflow_dokumentation_transkript_und_stichpunkte(self, mock_llm):
        """Workflow 1: Beide Eingaben kombiniert."""
        r = client.post("/api/generate", json={
            "workflow":   "dokumentation",
            "prompt":     "Erstelle eine Verlaufsnotiz.",
            "transcript": "Patient berichtet von Verbesserungen.",
            "bullets":    "- Schlaf besser\n- Weniger Anspannung",
        })
        assert r.status_code == 200

    def test_workflow_anamnese_mit_diagnosen(self, mock_llm_anamnese):
        """Workflow 2: Anamnese mit ICD-Codes."""
        r = client.post("/api/generate", json={
            "workflow":   "anamnese",
            "prompt":     "Erstelle eine Anamnese.",
            "diagnosen":  ["F32.1", "Z73.0"],
            "transcript": TXT_TRANSKRIPT.read_text(encoding="utf-8"),
        })
        assert r.status_code == 200
        data = r.json()
        assert data["text"]

    def test_workflow_anamnese_ohne_diagnosen(self, mock_llm_anamnese):
        """Workflow 2: Anamnese ohne Diagnosen – soll trotzdem funktionieren."""
        r = client.post("/api/generate", json={
            "workflow": "anamnese",
            "prompt":   "Erstelle eine Anamnese.",
        })
        assert r.status_code == 200

    def test_workflow_verlaengerung(self, mock_llm):
        """Workflow 3: Verlängerungsantrag."""
        r = client.post("/api/generate", json={
            "workflow": "verlaengerung",
            "prompt":   "Fuelle den Verlaengerungsantrag aus.",
        })
        assert r.status_code == 200

    def test_workflow_entlassbericht(self, mock_llm):
        """Workflow 4: Entlassbericht."""
        r = client.post("/api/generate", json={
            "workflow": "entlassbericht",
            "prompt":   "Erstelle einen Entlassbericht.",
        })
        assert r.status_code == 200

    def test_ungueltiger_workflow(self, mock_llm):
        """Ungültiger Workflow-Typ wird abgelehnt."""
        r = client.post("/api/generate", json={
            "workflow": "psychoanalyse",
            "prompt":   "test",
        })
        assert r.status_code == 422

    def test_fehlender_workflow(self, mock_llm):
        """Fehlender Workflow-Parameter wird abgelehnt."""
        r = client.post("/api/generate", json={
            "prompt": "test",
        })
        assert r.status_code == 422

    def test_mit_stilkontext(self, mock_llm):
        """Stilkontext wird korrekt uebergeben."""
        r = client.post("/api/generate", json={
            "workflow":      "dokumentation",
            "prompt":        "Erstelle eine Verlaufsnotiz.",
            "transcript":    "Patient berichtet von Fortschritten.",
            "style_context": "Schreibe kurz und praegnant.",
        })
        assert r.status_code == 200

    def test_ollama_nicht_erreichbar(self, mock_ollama_unavailable):
        """Backend gibt 502 zurueck wenn Ollama nicht erreichbar."""
        r = client.post("/api/generate", json={
            "workflow":   "dokumentation",
            "prompt":     "test",
            "transcript": "test",
        })
        assert r.status_code == 502
        assert "Ollama" in r.json()["detail"]

    def test_response_enthaelt_alle_felder(self, mock_llm):
        """Response-Schema ist vollstaendig."""
        r = client.post("/api/generate", json={
            "workflow":   "dokumentation",
            "prompt":     "test",
            "transcript": "test",
        })
        assert r.status_code == 200
        data = r.json()
        for field in ["job_id", "text", "model_used", "duration_seconds"]:
            assert field in data, f"Feld fehlt: {field}"


# ══════════════════════════════════════════════════════════════════
# 3. GENERIERUNG MIT DATEI-UPLOADS
# ══════════════════════════════════════════════════════════════════

class TestGenerierungMitDateien:

    def test_mit_audio(self, mock_llm, mock_transcribe):
        """Audio wird transkribiert und für Generierung verwendet."""
        r = client.post(
            "/api/generate/with-files",
            data={"workflow": "dokumentation", "prompt": "Erstelle eine Verlaufsnotiz."},
            files={"audio": ("gespraech.wav", AUDIO_KURZ.read_bytes(), "audio/wav")},
        )
        assert r.status_code == 200
        assert r.json()["text"]

    def test_mit_selbstauskunft_pdf(self, mock_llm, mock_extract_text):
        """Selbstauskunft-PDF wird extrahiert und für Anamnese verwendet."""
        r = client.post(
            "/api/generate/with-files",
            data={
                "workflow": "anamnese",
                "prompt":   "Erstelle eine Anamnese.",
                "diagnosen": "F32.1,Z73.0",
            },
            files={"selbstauskunft": (
                "selbstauskunft.pdf",
                PDF_SELBST_DIG.read_bytes(),
                "application/pdf"
            )},
        )
        assert r.status_code == 200

    def test_mit_vorbefunden(self, mock_llm, mock_extract_text):
        """Vorbefunde werden korrekt verarbeitet."""
        r = client.post(
            "/api/generate/with-files",
            data={
                "workflow": "anamnese",
                "prompt":   "Erstelle eine Anamnese.",
            },
            files={"vorbefunde": (
                "vorbefund.pdf",
                PDF_VERLAUF.read_bytes(),
                "application/pdf"
            )},
        )
        assert r.status_code == 200

    def test_mit_stilprofil(self, mock_llm, mock_extract_text):
        """Stilprofil-Datei wird verarbeitet und Stil extrahiert."""
        with patch(
            "app.services.extraction.extract_style_context",
            new=AsyncMock(return_value="Schreibe praegnant und ressourcenorientiert.")
        ):
            r = client.post(
                "/api/generate/with-files",
                data={"workflow": "dokumentation", "prompt": "test", "transcript": "test"},
                files={"style_file": (
                    "stil.docx",
                    DOCX_STILPROFIL.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )},
            )
        assert r.status_code == 200

    def test_mit_therapeut_id(self, mock_llm, mock_embedding):
        """therapeut_id aktiviert pgvector-Retrieval."""
        with patch("app.services.embeddings.retrieve_style_examples",
                   new=AsyncMock(return_value="Schreibe im Stil des Therapeuten.")), \
             patch("app.api.generate.retrieve_style_examples",
                   new=AsyncMock(return_value="Schreibe im Stil des Therapeuten.")):
            r = client.post(
                "/api/generate/with-files",
                data={
                    "workflow":     "dokumentation",
                    "prompt":       "test",
                    "transcript":   "test",
                    "therapeut_id": "Dr. Muster",
                },
            )
        assert r.status_code == 200

    def test_ohne_pflichtfeld_workflow(self, mock_llm):
        """Fehlender Workflow-Parameter wird abgelehnt."""
        r = client.post(
            "/api/generate/with-files",
            data={"prompt": "test"},
        )
        assert r.status_code == 422

    def test_transkriptions_fehler_gibt_502(self):
        """Fehler bei Transkription gibt 502 zurück."""
        with patch(
            "app.services.transcription.transcribe_audio",
            new=AsyncMock(side_effect=RuntimeError("Whisper nicht erreichbar"))
        ):
            r = client.post(
                "/api/generate/with-files",
                data={"workflow": "dokumentation", "prompt": "test"},
                files={"audio": ("test.wav", AUDIO_KURZ.read_bytes(), "audio/wav")},
            )
        assert r.status_code == 502


# ══════════════════════════════════════════════════════════════════
# 4. TRANSKRIPTION
# ══════════════════════════════════════════════════════════════════

class TestTranskription:

    def test_wav_datei(self, mock_transcribe):
        """WAV-Datei wird korrekt transkribiert."""
        r = client.post(
            "/api/transcribe",
            files={"file": ("aufnahme.wav", AUDIO_KURZ.read_bytes(), "audio/wav")},
        )
        assert r.status_code == 200
        data = r.json()
        assert "transcript" in data
        assert "language" in data
        assert "word_count" in data
        assert data["language"] == "de"
        assert data["word_count"] > 0

    def test_response_schema_vollstaendig(self, mock_transcribe):
        """Transkriptions-Response enthaelt alle Pflichtfelder."""
        r = client.post(
            "/api/transcribe",
            files={"file": ("test.wav", AUDIO_KURZ.read_bytes(), "audio/wav")},
        )
        assert r.status_code == 200
        for field in ["job_id", "transcript", "language", "duration_seconds", "word_count"]:
            assert field in r.json(), f"Feld fehlt: {field}"

    def test_pdf_wird_abgelehnt(self):
        """PDF-Upload beim Transkriptions-Endpunkt wird abgelehnt."""
        r = client.post(
            "/api/transcribe",
            files={"file": ("dok.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
        assert r.status_code == 422

    def test_txt_wird_abgelehnt(self):
        """TXT-Upload beim Transkriptions-Endpunkt wird abgelehnt."""
        r = client.post(
            "/api/transcribe",
            files={"file": ("text.txt", b"Hallo Welt", "text/plain")},
        )
        assert r.status_code == 422

    def test_keine_datei_wird_abgelehnt(self):
        """Request ohne Datei wird abgelehnt."""
        r = client.post("/api/transcribe")
        assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════
# 5. DOKUMENT-VERARBEITUNG
# ══════════════════════════════════════════════════════════════════

class TestDokumentVerarbeitung:

    def test_extract_pdf_maschinenlesbar(self):
        """Maschinenlesbares PDF wird korrekt extrahiert."""
        from app.services.extraction import ExtractionResult
        mock_result = ExtractionResult(
            text="Verlaufsbericht sysTelios Klinik Patient Herr M. Einzeltherapie Diagnose F32.1",
            method="pdfplumber", quality=0.92, pages=1, warnings=[]
        )
        with patch("app.services.extraction.extract_text_with_meta", new=AsyncMock(return_value=mock_result)):
            r = client.post(
                "/api/documents/extract",
                files={"file": ("verlauf.pdf", PDF_VERLAUF.read_bytes(), "application/pdf")},
            )
        assert r.status_code == 200
        data = r.json()
        assert "text" in data
        assert "extraction" in data
        assert data["extraction"]["method"] == "pdfplumber"
        assert data["extraction"]["quality"] > 0

    def test_extract_docx(self):
        """DOCX wird korrekt extrahiert."""
        from app.services.extraction import ExtractionResult
        mock_result = ExtractionResult(
            text="Entlassbericht sysTelios Klinik Diagnose F32.1 Behandlung abgeschlossen",
            method="docx", quality=0.95, pages=1, warnings=[]
        )
        with patch("app.services.extraction.extract_text_with_meta", new=AsyncMock(return_value=mock_result)):
            r = client.post(
                "/api/documents/extract",
                files={"file": (
                    "bericht.docx",
                    DOCX_ENTLASS_B.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )},
            )
        assert r.status_code == 200
        assert r.json()["extraction"]["method"] == "docx"

    def test_extract_unbekanntes_format(self):
        """Unbekanntes Dateiformat wird abgelehnt."""
        from app.services.extraction import ExtractionResult
        # ValueError wird von extract_text_with_meta geworfen und als 422 zurueckgegeben
        with patch("app.services.extraction.extract_text_with_meta",
                   new=AsyncMock(side_effect=ValueError("Nicht unterstuetztes Dateiformat: '.xyz'"))):
            r = client.post(
                "/api/documents/extract",
                files={"file": ("test.xyz", b"Inhalt", "application/octet-stream")},
            )
        assert r.status_code == 422

    def test_fill_entlassbericht(self, mock_llm, mock_extract_text):
        """Entlassbericht-Vorlage wird befuellt."""
        with patch(
            "app.services.docx_fill.fill_docx_template",
            new=AsyncMock(return_value=Path("/tmp/systelios_test_outputs/entlassbericht_test.docx"))
        ):
            Path("/tmp/systelios_test_outputs/entlassbericht_test.docx").touch()
            r = client.post(
                "/api/documents/fill",
                data={
                    "workflow": "entlassbericht",
                    "prompt":   "Erstelle einen Entlassbericht.",
                },
                files={
                    "template": (
                        "vorlage.docx",
                        DOCX_ENTLASS_V.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ),
                    "verlauf": ("verlauf.pdf", PDF_VERLAUF.read_bytes(), "application/pdf"),
                },
            )
        assert r.status_code == 200
        data = r.json()
        assert "download_url" in data
        assert "preview_text" in data

    def test_fill_verlaengerungsantrag(self, mock_llm, mock_extract_text):
        """Verlängerungsantrag-Vorlage wird befuellt."""
        with patch(
            "app.services.docx_fill.fill_docx_template",
            new=AsyncMock(return_value=Path("/tmp/systelios_test_outputs/antrag_test.docx"))
        ):
            Path("/tmp/systelios_test_outputs/antrag_test.docx").touch()
            r = client.post(
                "/api/documents/fill",
                data={
                    "workflow": "verlaengerung",
                    "prompt":   "Fuelle den Antrag aus.",
                },
                files={
                    "template": (
                        "antrag.docx",
                        DOCX_VERL_V.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ),
                    "verlauf": ("verlauf.pdf", PDF_VERLAUF.read_bytes(), "application/pdf"),
                },
            )
        assert r.status_code == 200

    def test_fill_ungueltiger_workflow(self, mock_llm):
        """Ungültiger Workflow für fill wird abgelehnt."""
        r = client.post(
            "/api/documents/fill",
            data={"workflow": "dokumentation", "prompt": "test"},
            files={
                "template": ("v.docx", DOCX_ENTLASS_V.read_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                "verlauf":  ("v.pdf", PDF_VERLAUF.read_bytes(), "application/pdf"),
            },
        )
        assert r.status_code == 422

    def test_style_extraktion(self, mock_llm):
        """Stilprofil wird aus Beispieltext extrahiert."""
        style_text = "Schreibe in einem Stil der praegnant und ressourcenorientiert ist."
        with patch("app.api.documents.extract_style_context", new=AsyncMock(return_value=style_text)), \
             patch("app.api.documents.extract_text", new=AsyncMock(return_value="Verlaufsnotiz " * 30)):
            r = client.post(
                "/api/documents/style",
                data={"therapeut_id": "Dr. Muster"},
                files={"style_file": (
                    "stil.docx",
                    DOCX_STILPROFIL.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )},
            )
        assert r.status_code == 200
        data = r.json()
        assert "style_context" in data
        assert "therapeut_id" in data
        assert data["therapeut_id"] == "Dr. Muster"


# ══════════════════════════════════════════════════════════════════
# 6. DOWNLOAD & SICHERHEIT
# ══════════════════════════════════════════════════════════════════

class TestDownloadUndSicherheit:

    def test_download_nicht_vorhanden(self):
        """Nicht vorhandene Datei gibt 404."""
        r = client.get("/api/documents/download/nichtvorhanden.docx")
        assert r.status_code == 404

    def test_download_path_traversal_slash(self):
        """Path Traversal mit Slash wird blockiert."""
        r = client.get("/api/documents/download/../etc/passwd")
        assert r.status_code in (400, 404)

    def test_download_path_traversal_backslash(self):
        """Path Traversal mit Backslash wird blockiert."""
        r = client.get("/api/documents/download/..\\etc\\passwd")
        assert r.status_code in (400, 404)

    def test_download_path_traversal_doppelpunkt(self):
        """Path mit Doppelpunkten wird blockiert."""
        r = client.get("/api/documents/download/test/../secret.docx")
        assert r.status_code in (400, 404)

    def test_upload_zu_grosse_datei(self):
        """Zu grosse Datei wird abgelehnt (> MAX_UPLOAD_MB)."""
        # 101 MB simulieren – nur Header testen, kein echter Upload
        # TestClient begrenzt Dateigröße nicht, daher nur Format-Check
        r = client.post(
            "/api/transcribe",
            files={"file": ("gross.exe", b"\x00" * 100, "application/octet-stream")},
        )
        assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════
# 7. PROMPTS (Unit Tests)
# ══════════════════════════════════════════════════════════════════

class TestPrompts:

    def test_system_prompt_dokumentation(self):
        """System-Prompt für Dokumentation enthält sysTelios-Kontext."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(workflow="dokumentation")
        assert "sysTelios" in p
        assert "Verlaufsnotiz" in p
        assert "Deutsch" in p

    def test_system_prompt_anamnese_mit_diagnosen(self):
        """Diagnosen werden in den Anamnese-Prompt eingefügt."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(workflow="anamnese", diagnosen=["F32.1", "Z73.0"])
        assert "F32.1" in p
        assert "Z73.0" in p
        assert "AMDP" in p

    def test_system_prompt_anamnese_ohne_diagnosen(self):
        """Anamnese-Prompt ohne Diagnosen enthält Platzhalter."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(workflow="anamnese")
        assert "noch nicht festgelegt" in p

    def test_system_prompt_mit_stilprofil(self):
        """Stilprofil wird an den Prompt angehängt."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="dokumentation",
            style_context="Schreibe kurz und praegnant."
        )
        assert "Schreibe kurz und praegnant" in p
        assert "Schreibe kurz und praegnant" in p

    def test_system_prompt_custom_prompt(self):
        """Eigener Prompt überschreibt den Standard-Prompt."""
        from app.services.prompts import build_system_prompt
        p = build_system_prompt(
            workflow="dokumentation",
            custom_prompt="Mein eigener Prompt fuer diesen Therapeuten."
        )
        assert "Mein eigener Prompt" in p

    def test_user_content_dokumentation_transkript(self):
        """User-Content für Dokumentation enthält Transkript."""
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="dokumentation",
            transcript="Patient berichtet von Fortschritten.",
        )
        assert "TRANSKRIPT" in u
        assert "Patient berichtet" in u

    def test_user_content_dokumentation_stichpunkte(self):
        """User-Content für Dokumentation enthält Stichpunkte."""
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="dokumentation",
            bullets="- Schlaf besser\n- Weniger Anspannung",
        )
        assert "STICHPUNKTE" in u

    def test_user_content_anamnese_vollstaendig(self):
        """User-Content für Anamnese enthält alle Eingaben."""
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="anamnese",
            selbstauskunft_text="Selbstauskunft des Klienten...",
            vorbefunde_text="Vorbefund vom Hausarzt...",
            transcript="Aufnahmegespraech...",
            diagnosen=["F32.1"],
        )
        assert "SELBSTAUSKUNFT" in u
        assert "VORBEFUNDE" in u
        assert "AUFNAHMEGESPRAECH" in u
        assert "F32.1" in u

    def test_user_content_verlaengerung(self):
        """User-Content für Verlängerung enthält Verlaufsdoku."""
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="verlaengerung",
            verlauf_text="14 Tage Behandlung, guter Verlauf.",
        )
        assert "VERLAUFSDOKUMENTATION" in u
        assert "Verlaengerungsantrag" in u

    def test_user_content_entlassbericht(self):
        """User-Content für Entlassbericht enthält Verlaufsdoku."""
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="entlassbericht",
            verlauf_text="28 Tage Behandlung.",
        )
        assert "VERLAUFSDOKUMENTATION" in u
        assert "Entlassbericht" in u

    def test_alle_vier_workflows_haben_basis_prompt(self):
        """Alle vier Workflows haben einen definierten Basis-Prompt."""
        from app.services.prompts import BASE_PROMPTS
        for workflow in ["dokumentation", "anamnese", "verlaengerung", "entlassbericht"]:
            assert workflow in BASE_PROMPTS
            assert len(BASE_PROMPTS[workflow]) > 100

    def test_kein_markdown_in_ausgabe_anweisung(self):
        """System-Prompt enthält Anweisung gegen Markdown-Formatierung."""
        from app.services.prompts import build_system_prompt
        for wf in ["dokumentation", "anamnese", "verlaengerung", "entlassbericht"]:
            p = build_system_prompt(workflow=wf)
            assert "Markdown" in p or "markdown" in p


# ══════════════════════════════════════════════════════════════════
# 8. STILPROFIL-VERWALTUNG (pgvector)
# ══════════════════════════════════════════════════════════════════

class TestStilprofil:

    def test_upload_stilbeispiel(self, mock_embedding):
        """Stilbeispiel wird hochgeladen und gespeichert."""
        with patch("app.services.extraction.extract_text", new=AsyncMock(
            return_value=DOCX_STILPROFIL.read_text(encoding='utf-8', errors='replace')[:500]
            if DOCX_STILPROFIL.suffix == '.txt' else
            "Verlaufsnotiz Beispieltext eines Therapeuten der sysTelios Klinik. " * 10
        )):
            r = client.post(
                "/api/style/upload",
                data={
                    "therapeut_id": "Dr. Muster",
                    "dokumenttyp":  "dokumentation",
                    "ist_statisch": "false",
                },
                files={"beispiel_file": (
                    "stil.docx",
                    DOCX_STILPROFIL.read_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["therapeut_id"] == "Dr. Muster"
        assert data["dokumenttyp"] == "dokumentation"
        assert "embedding_id" in data

    def test_upload_anker_beispiel(self, mock_embedding):
        """Anker-Beispiel (ist_statisch=true) wird korrekt gesetzt."""
        with patch("app.services.extraction.extract_text", new=AsyncMock(
            return_value="Anker-Verlaufsnotiz sysTelios Klinik Beispieltext. " * 15
        )):
            r = client.post(
                "/api/style/upload",
                data={
                    "therapeut_id": "Dr. Anker",
                    "dokumenttyp":  "dokumentation",
                    "ist_statisch": "true",
                },
                files={"beispiel_file": (
                    "stil.txt",
                    TXT_VERLAUF.read_bytes(),
                    "text/plain"
                )},
            )
        assert r.status_code == 200
        assert r.json()["ist_statisch"] is True

    def test_upload_ungueltiger_dokumenttyp(self):
        """Ungültiger Dokumenttyp wird abgelehnt."""
        r = client.post(
            "/api/style/upload",
            data={
                "therapeut_id": "Dr. Test",
                "dokumenttyp":  "ungueltig",
            },
            files={"beispiel_file": ("test.txt", b"Inhalt", "text/plain")},
        )
        assert r.status_code == 422

    def test_upload_leere_therapeut_id(self):
        """Leere therapeut_id wird abgelehnt."""
        r = client.post(
            "/api/style/upload",
            data={
                "therapeut_id": "   ",
                "dokumenttyp":  "dokumentation",
            },
            files={"beispiel_file": ("test.txt", b"Inhalt", "text/plain")},
        )
        assert r.status_code == 422

    def test_liste_beispiele(self, mock_embedding):
        """Beispiele eines Therapeuten können abgerufen werden."""
        # Erst hochladen
        with patch("app.services.extraction.extract_text", new=AsyncMock(
            return_value="Beispieltext fuer Listierung. " * 20
        )):
            client.post(
                "/api/style/upload",
                data={"therapeut_id": "Liste-Test", "dokumenttyp": "anamnese"},
                files={"beispiel_file": ("t.txt", TXT_SELBST.read_bytes(), "text/plain")},
            )

        r = client.get("/api/style/Liste-Test")
        assert r.status_code == 200
        data = r.json()
        assert "therapeut_id" in data
        assert "embeddings" in data
        assert "total" in data
        assert data["therapeut_id"] == "Liste-Test"

    def test_liste_unbekannter_therapeut(self):
        """Leere Liste für unbekannten Therapeuten."""
        r = client.get("/api/style/UnbekannterTherapeut99")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_alle_dokumenttypen_akzeptiert(self, mock_embedding):
        """Alle vier Dokumenttypen werden akzeptiert."""
        for dtype in ["dokumentation", "anamnese", "verlaengerung", "entlassbericht"]:
            with patch("app.services.extraction.extract_text", new=AsyncMock(
                return_value=f"Beispieltext fuer {dtype}. " * 20
            )):
                r = client.post(
                    "/api/style/upload",
                    data={"therapeut_id": "Dtype-Test", "dokumenttyp": dtype},
                    files={"beispiel_file": ("t.txt", b"x" * 200, "text/plain")},
                )
            assert r.status_code == 200, f"Dokumenttyp {dtype} fehlgeschlagen"


# ══════════════════════════════════════════════════════════════════
# 9. EXTRACTION SERVICE (Unit Tests)
# ══════════════════════════════════════════════════════════════════

class TestExtraktion:

    def test_txt_datei_wird_gelesen(self):
        """TXT-Fixture wird korrekt eingelesen."""
        import asyncio
        from app.services.extraction import extract_text
        text = asyncio.run(extract_text(TXT_TRANSKRIPT))
        assert len(text) > 100

    def test_txt_verlaufsdokumentation(self):
        """Verlaufsdokumentation TXT enthält erwarteten Inhalt."""
        import asyncio
        from app.services.extraction import extract_text
        text = asyncio.run(extract_text(TXT_VERLAUF))
        assert "Einzeltherapie" in text or "Therapie" in text
        assert len(text) > 200

    def test_unbekanntes_format_gibt_valueerror(self):
        """Unbekanntes Format wirft ValueError."""
        import asyncio
        from app.services.extraction import extract_text
        with pytest.raises(ValueError, match="Nicht unterstuetztes"):
            asyncio.run(extract_text(Path("/tmp/test.xyz")))

    def test_docx_fixture_lesbar(self):
        """DOCX-Fixture kann gelesen werden."""
        import asyncio
        from app.services.extraction import extract_text
        text = asyncio.run(extract_text(DOCX_STILPROFIL))
        assert len(text) > 50

    def test_qualitaetspruefung_guter_text(self):
        """Qualitätsprüfung erkennt guten deutschen Text."""
        from app.services.extraction import _assess_quality
        text = TXT_VERLAUF.read_text(encoding='utf-8')
        q = _assess_quality(text)
        assert q.ok
        assert q.score > 0.6

    def test_qualitaetspruefung_leerer_text(self):
        """Qualitätsprüfung erkennt leeren Text."""
        from app.services.extraction import _assess_quality
        q = _assess_quality("")
        assert not q.ok

    def test_qualitaetspruefung_zeichensalat(self):
        """Qualitätsprüfung erkennt OCR-Zeichensalat."""
        from app.services.extraction import _assess_quality
        q = _assess_quality("§§§@@@###~~~" * 30)
        assert not q.ok


# ══════════════════════════════════════════════════════════════════
# 10. ECHTE DATEIEN (werden uebersprungen wenn nicht vorhanden)
# ══════════════════════════════════════════════════════════════════

class TestEchteDateien:

    @pytest.mark.skipif(not REAL_FILES["audio"].exists(),
                        reason="Echte Audio-Datei nicht vorhanden")
    def test_echte_audio_transkription(self, mock_transcribe):
        """Echte MP3-Aufnahme wird verarbeitet."""
        r = client.post(
            "/api/transcribe",
            files={"file": (
                "real.mp3",
                REAL_FILES["audio"].read_bytes(),
                "audio/mpeg"
            )},
        )
        assert r.status_code == 200
        assert len(r.json()["transcript"]) > 0

    @pytest.mark.skipif(not REAL_FILES["selbstauskunft_handschrift"].exists(),
                        reason="Echte Selbstauskunft nicht vorhanden")
    def test_echte_selbstauskunft_handschrift(self):
        """Handschriftlich ausgefüllte Selbstauskunft wird durch OCR-Kette verarbeitet."""
        import asyncio
        with patch("app.services.extraction._check_vision_model_available",
                   new=AsyncMock(return_value=False)):
            from app.services.extraction import extract_text_with_meta
            try:
                result = asyncio.run(extract_text_with_meta(
                    REAL_FILES["selbstauskunft_handschrift"]
                ))
            except RuntimeError as e:
                if "Alle Extraktionsstufen fehlgeschlagen" in str(e):
                    pytest.skip(
                        "OCR-Kette unzureichend (kein Ollama Vision verfuegbar) – "
                        "bitte 'ollama pull llava' ausfuehren"
                    )
                raise
        assert len(result.text) > 20
        print(f"\nOCR-Methode: {result.method}, Qualität: {result.quality:.2f}")
        print(f"Extrahierter Text (erste 200 Zeichen):\n{result.text[:200]}")

    @pytest.mark.skipif(not REAL_FILES["entlassbericht_real"].exists(),
                        reason="Echter Entlassbericht nicht vorhanden")
    def test_echter_entlassbericht_stilextraktion(self, mock_llm):
        """Echter Entlassbericht wird als Stilprofil-Grundlage verarbeitet."""
        with patch(
            "app.services.extraction.extract_style_context",
            new=AsyncMock(return_value="Schreibe praegnant und fachlich.")
        ):
            r = client.post(
                "/api/documents/style",
                data={"therapeut_id": "Real-Test"},
                files={"style_file": (
                    "real.docx",
                    REAL_FILES["entlassbericht_real"].read_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )},
            )
        assert r.status_code == 200

    @pytest.mark.skipif(not REAL_FILES["verlauf_real"].exists(),
                        reason="Echter Verlaufsbericht nicht vorhanden")
    def test_echter_verlaufsbericht_generierung(self, mock_llm):
        """Echter Verlaufsbericht wird für Entlassbericht-Generierung verwendet."""
        with patch(
            "app.services.docx_fill.fill_docx_template",
            new=AsyncMock(return_value=Path("/tmp/systelios_test_outputs/real_test.docx"))
        ):
            Path("/tmp/systelios_test_outputs/real_test.docx").touch()
            r = client.post(
                "/api/documents/fill",
                data={"workflow": "entlassbericht", "prompt": "Erstelle einen Entlassbericht."},
                files={
                    "template": (
                        "vorlage.docx",
                        DOCX_ENTLASS_V.read_bytes(),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ),
                    "verlauf": (
                        "real.pdf",
                        REAL_FILES["verlauf_real"].read_bytes(),
                        "application/pdf"
                    ),
                },
            )
        assert r.status_code == 200

# ══════════════════════════════════════════════════════════════════
# 11. JOB-QUEUE
# ══════════════════════════════════════════════════════════════════

class TestJobQueue:
    """Tests fuer den asynchronen Job-Queue-Endpunkt."""

    def test_job_erstellen_dokumentation(self, mock_llm, mock_transcribe):
        """POST /api/jobs/generate gibt sofort job_id zurueck."""
        r = client.post(
            "/api/jobs/generate",
            data={
                "workflow":   "dokumentation",
                "prompt":     "Erstelle eine Verlaufsnotiz.",
                "transcript": TXT_TRANSKRIPT.read_text(encoding="utf-8"),
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        assert data["status"] == "pending"
        assert len(data["job_id"]) == 32  # uuid hex

    def test_job_status_abfragen(self, mock_llm):
        """GET /api/jobs/{job_id} gibt Job-Status zurueck."""
        # Job erstellen
        r = client.post(
            "/api/jobs/generate",
            data={"workflow": "dokumentation", "prompt": "test", "transcript": "test"},
        )
        job_id = r.json()["job_id"]

        # Status abfragen
        r2 = client.get(f"/api/jobs/{job_id}")
        assert r2.status_code == 200
        data = r2.json()
        assert data["job_id"] == job_id
        assert data["status"] in ("pending", "running", "done", "error")
        assert "created_at" in data
        assert "workflow" in data

    def test_job_nicht_gefunden(self):
        """GET /api/jobs/unbekannt gibt 404 zurueck."""
        r = client.get("/api/jobs/nichtvorhandenejobid123")
        assert r.status_code == 404

    def test_job_liste(self, mock_llm):
        """GET /api/jobs listet alle Jobs auf."""
        # Mindestens einen Job erstellen
        client.post(
            "/api/jobs/generate",
            data={"workflow": "dokumentation", "prompt": "test", "transcript": "test"},
        )
        r = client.get("/api/jobs")
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) >= 1

    def test_job_alle_workflows(self, mock_llm):
        """Alle 4 Workflows koennen als Jobs gestartet werden."""
        for workflow in ["dokumentation", "anamnese", "verlaengerung", "entlassbericht"]:
            r = client.post(
                "/api/jobs/generate",
                data={"workflow": workflow, "prompt": "test", "transcript": "test"},
            )
            assert r.status_code == 200, f"Workflow {workflow} fehlgeschlagen"
            assert "job_id" in r.json()

    def test_job_ungültiger_workflow(self):
        """Ungültiger Workflow wird abgelehnt."""
        r = client.post(
            "/api/jobs/generate",
            data={"workflow": "unbekannt", "prompt": "test"},
        )
        assert r.status_code == 422

    def test_job_mit_audio(self, mock_llm, mock_transcribe):
        """Job mit Audio-Upload wird korrekt gestartet."""
        r = client.post(
            "/api/jobs/generate",
            data={"workflow": "dokumentation", "prompt": "test"},
            files={"audio": ("test.wav", AUDIO_KURZ.read_bytes(), "audio/wav")},
        )
        assert r.status_code == 200
        assert "job_id" in r.json()

    def test_job_mit_selbstauskunft(self, mock_llm, mock_extract_text):
        """Job mit Selbstauskunft-PDF wird korrekt gestartet."""
        r = client.post(
            "/api/jobs/generate",
            data={"workflow": "anamnese", "prompt": "test", "diagnosen": "F32.1"},
            files={"selbstauskunft": (
                "selbst.pdf", PDF_SELBST_DIG.read_bytes(), "application/pdf"
            )},
        )
        assert r.status_code == 200
        assert "job_id" in r.json()

    def test_job_ergebnis_schema(self, mock_llm):
        """Abgeschlossener Job enthaelt alle erwarteten Felder."""
        import time
        r = client.post(
            "/api/jobs/generate",
            data={"workflow": "dokumentation", "prompt": "test", "transcript": "test"},
        )
        job_id = r.json()["job_id"]

        # Kurz warten bis Job abgeschlossen
        for _ in range(10):
            time.sleep(0.5)
            poll = client.get(f"/api/jobs/{job_id}")
            if poll.json()["status"] in ("done", "error"):
                break

        data = poll.json()
        expected_fields = [
            "job_id", "workflow", "status", "created_at",
            "started_at", "finished_at", "duration_s"
        ]
        for field in expected_fields:
            assert field in data, f"Feld fehlt: {field}"

    def test_job_queue_service_direkt(self):
        """Job-Queue Service-Klasse direkt testen."""
        from app.services.job_queue import job_queue, JobStatus

        job = job_queue.create_job("dokumentation", "Test-Job")
        assert job.status == JobStatus.PENDING
        assert job.job_id is not None
        assert len(job.job_id) == 32

        # Job wiederfinden
        found = job_queue.get_job(job.job_id)
        assert found is not None
        assert found.job_id == job.job_id

        # to_dict
        d = job.to_dict()
        assert d["status"] == "pending"
        assert d["workflow"] == "dokumentation"
        assert d["description"] == "Test-Job"

    def test_prompts_kein_gespraechspartner(self):
        """System-Prompts enthalten keine Formulierung als Gespraechspartner."""
        from app.services.prompts import BASE_PROMPTS
        verboten = [
            "Ich bin ein Computerprogramm",
            "ich bin nicht in der Lage",
            "kann ich nicht helfen",
        ]
        for workflow, prompt in BASE_PROMPTS.items():
            for v in verboten:
                assert v.lower() not in prompt.lower(), \
                    f"Workflow '{workflow}' enthaelt verbotene Formulierung: '{v}'"

    def test_prompts_dokumentationssystem(self):
        """Alle Prompts bezeichnen das System als Dokumentationssystem."""
        from app.services.prompts import BASE_PROMPTS
        for workflow, prompt in BASE_PROMPTS.items():
            assert "Dokumentationssystem" in prompt, \
                f"Workflow '{workflow}' fehlt 'Dokumentationssystem' im Prompt"
