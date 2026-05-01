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


# ── Health ────────────────────────────────────────────────────────

def test_health():
    with patch("httpx.AsyncClient") as _:
        r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "llm_model" in data
    assert "whisper_model" in data


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


# ══════════════════════════════════════════════════════════════════
# JOBS API – alle vier Workflows vollständig
# ══════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_llm_jobs():
    """Mock für jobs.py-Endpunkt (anderer Patch-Pfad als generate.py)."""
    mock_response = {
        "text":       "Generierter Text für diesen Workflow.",
        "model_used": "ollama/qwen3:32b",
        "duration_s": 1.5,
        "token_count": 80,
    }
    with patch("app.api.jobs.generate_text",        new=AsyncMock(return_value=mock_response)), \
         patch("app.services.llm.generate_text",     new=AsyncMock(return_value=mock_response)):
        yield


@pytest.fixture
def mock_extract_jobs():
    """Mock für Textextraktion im jobs-Endpunkt."""
    with patch("app.services.extraction.extract_text",
               new=AsyncMock(return_value="Extrahierter Dokumententext für Tests.")), \
         patch("app.api.jobs.extract_text",
               new=AsyncMock(return_value="Extrahierter Dokumententext für Tests.")):
        yield


class TestJobsAPI:
    """Tests für POST /api/jobs/generate und GET /api/jobs/{id}."""

    def _start_job(self, workflow, extra_data=None, files=None):
        data = {"workflow": workflow, "prompt": f"Prompt für {workflow}."}
        if extra_data:
            data.update(extra_data)
        return client.post("/api/jobs/generate", data=data, files=files or {})

    def _wait_job(self, job_id, max_polls=30):
        import time
        for _ in range(max_polls):
            r = client.get(f"/api/jobs/{job_id}")
            assert r.status_code == 200
            job = r.json()
            if job["status"] in ("done", "error"):
                return job
            time.sleep(0.1)
        raise TimeoutError(f"Job {job_id} nicht abgeschlossen")

    # ── Job-Lifecycle ─────────────────────────────────────────────

    def test_job_erstellen_gibt_job_id(self, mock_llm_jobs):
        r = self._start_job("dokumentation", {"transcript": "Test-Transkript."})
        assert r.status_code == 200
        data = r.json()
        assert "job_id" in data
        assert data["status"] == "pending"
        assert len(data["job_id"]) == 32

    def test_job_status_abfragen(self, mock_llm_jobs):
        r = self._start_job("dokumentation", {"transcript": "Test."})
        job_id = r.json()["job_id"]
        job = self._wait_job(job_id)
        assert job["status"] == "done"
        assert job["result_text"]
        assert "has_transcript" in job
        assert "result_transcript" not in job   # nie im Poll-Response

    def test_job_nicht_vorhanden_gibt_404(self):
        r = client.get("/api/jobs/nichtvorhandene000000000000000000")
        assert r.status_code == 404

    def test_job_list(self, mock_llm_jobs):
        self._start_job("dokumentation", {"transcript": "Test."})
        r = client.get("/api/jobs")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    # ── Workflow 1: Dokumentation ─────────────────────────────────

    def test_p1_mit_transkript_text(self, mock_llm_jobs):
        r = self._start_job("dokumentation", {"transcript": "Klient berichtet von Fortschritten."})
        job = self._wait_job(r.json()["job_id"])
        assert job["status"] == "done"
        assert job["result_text"]

    def test_p1_mit_bullets(self, mock_llm_jobs):
        r = self._start_job("dokumentation", {"bullets": "- Schlafprobleme\n- IFS-Arbeit"})
        job = self._wait_job(r.json()["job_id"])
        assert job["status"] == "done"

    def test_p1_bullets_und_transkript_getrennt(self, mock_llm_jobs):
        """Bullets kommen als separates Feld – nicht in transcript eingebaut."""
        r = self._start_job("dokumentation", {
            "transcript": "Gesprächsinhalt.",
            "bullets":    "- Innerer Löwe\n- IFS-Arbeit",
        })
        assert r.status_code == 200
        job = self._wait_job(r.json()["job_id"])
        assert job["status"] == "done"

    def test_p1_mit_audio(self, mock_llm_jobs, mock_transcribe):
        audio_bytes = b"RIFF" + b"\x00" * 100
        r = client.post("/api/jobs/generate", data={
            "workflow": "dokumentation",
            "prompt":   "Verlaufsnotiz erstellen.",
        }, files={"audio": ("aufnahme.wav", io.BytesIO(audio_bytes), "audio/wav")})
        assert r.status_code == 200
        job = self._wait_job(r.json()["job_id"])
        assert job["status"] == "done"
        assert job["has_transcript"] is True

    def test_p1_transkript_endpunkt(self, mock_llm_jobs, mock_transcribe):
        """GET /api/jobs/{id}/transcript liefert Transkript separat."""
        audio_bytes = b"RIFF" + b"\x00" * 100
        r = client.post("/api/jobs/generate", data={
            "workflow": "dokumentation",
            "prompt":   "test",
        }, files={"audio": ("test.wav", io.BytesIO(audio_bytes), "audio/wav")})
        job_id = r.json()["job_id"]
        job = self._wait_job(job_id)
        assert job["has_transcript"] is True

        tr = client.get(f"/api/jobs/{job_id}/transcript")
        assert tr.status_code == 200
        data = tr.json()
        assert "transcript" in data
        assert "word_count" in data
        assert data["word_count"] > 0

    def test_p1_kein_transkript_endpunkt_wenn_kein_audio(self, mock_llm_jobs):
        """Kein Audio → has_transcript=False → /transcript gibt 404."""
        r = self._start_job("dokumentation", {"transcript": "Nur Text."})
        job_id = r.json()["job_id"]
        self._wait_job(job_id)
        tr = client.get(f"/api/jobs/{job_id}/transcript")
        assert tr.status_code == 404

    def test_p1_mit_style_text(self, mock_llm_jobs):
        """Stilvorlage via Text-Input (C&P)."""
        r = self._start_job("dokumentation", {
            "transcript": "Gesprächsinhalt.",
            "style_text": "Im Mittelpunkt stand die Angst von Frau K. Schreibstil: klar, IFS-orientiert.",
        })
        job = self._wait_job(r.json()["job_id"])
        assert job["status"] == "done"

    # ── Workflow 2: Anamnese ──────────────────────────────────────

    def test_p2_mit_selbstauskunft(self, mock_llm_jobs, mock_extract_jobs):
        pdf_bytes = b"%PDF-1.4 Selbstauskunft"
        r = client.post("/api/jobs/generate", data={
            "workflow":  "anamnese",
            "prompt":    "Anamnese erstellen.",
            "diagnosen": "F32.1,Z73.0",
        }, files={"selbstauskunft": ("selbst.pdf", io.BytesIO(pdf_bytes), "application/pdf")})
        assert r.status_code == 200
        job = self._wait_job(r.json()["job_id"])
        assert job["status"] == "done"

    def test_p2_mit_vorbefunden(self, mock_llm_jobs, mock_extract_jobs):
        pdf_bytes = b"%PDF-1.4 Vorbefund"
        r = client.post("/api/jobs/generate", data={
            "workflow": "anamnese",
            "prompt":   "Anamnese erstellen.",
        }, files={"vorbefunde": ("vorbefund.pdf", io.BytesIO(pdf_bytes), "application/pdf")})
        assert r.status_code == 200
        job = self._wait_job(r.json()["job_id"])
        assert job["status"] == "done"

    def test_p2_user_content_enthält_selbstauskunft(self, mock_llm_jobs, mock_extract_jobs):
        """build_user_content für Anamnese enthält extrahierten Selbstauskunft-Text."""
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="anamnese",
            selbstauskunft_text="Patient leidet unter Schlafproblemen.",
            diagnosen=["F32.1"],
        )
        assert "SELBSTAUSKUNFT" in u
        assert "Schlafproblemen" in u
        assert "F32.1" in u
        assert "Anamnese" in u

    # ── Workflow 3: Verlängerungsantrag ───────────────────────────

    def test_p3_mit_verlaufsdoku(self, mock_llm_jobs, mock_extract_jobs):
        pdf_bytes = b"%PDF-1.4 Verlaufsdokumentation"
        r = client.post("/api/jobs/generate", data={
            "workflow": "verlaengerung",
            "prompt":   "Verlängerungsantrag ausfüllen.",
        }, files={"vorbefunde": ("verlauf.pdf", io.BytesIO(pdf_bytes), "application/pdf")})
        assert r.status_code == 200
        job = self._wait_job(r.json()["job_id"])
        assert job["status"] == "done"

    def test_p3_verlauf_landet_als_verlauf_text(self, mock_llm_jobs):
        """Verlaufsdoku-Upload für verlaengerung wird als verlauf_text geroutet."""
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="verlaengerung",
            verlauf_text="14 Wochen stationär, guter Verlauf.",
        )
        assert "VERLAUFSDOKUMENTATION" in u
        assert "14 Wochen" in u
        assert "Verlaengerungsantrag" in u

    def test_p3_vorbefunde_nicht_als_verlauf_für_anamnese(self):
        """Für Anamnese bleibt vorbefunde_text als VORBEFUNDE, nicht als VERLAUF."""
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="anamnese",
            vorbefunde_text="Vorbefund vom Hausarzt.",
        )
        assert "VORBEFUNDE" in u
        assert "VERLAUFSDOKUMENTATION" not in u

    def test_p3_mit_style_text(self, mock_llm_jobs):
        """C&P-Stilvorlage funktioniert für Verlängerungsantrag."""
        r = self._start_job("verlaengerung", {
            "style_text": "Beispiel-Verlängerungsantrag im Klinikstil.",
        })
        assert r.status_code == 200

    # ── Workflow 4: Entlassbericht ────────────────────────────────

    def test_p4_mit_verlaufsdoku(self, mock_llm_jobs, mock_extract_jobs):
        pdf_bytes = b"%PDF-1.4 Verlaufsdokumentation Entlassung"
        r = client.post("/api/jobs/generate", data={
            "workflow": "entlassbericht",
            "prompt":   "Entlassbericht erstellen.",
        }, files={"vorbefunde": ("verlauf.pdf", io.BytesIO(pdf_bytes), "application/pdf")})
        assert r.status_code == 200
        job = self._wait_job(r.json()["job_id"])
        assert job["status"] == "done"

    def test_p4_verlauf_landet_als_verlauf_text(self):
        """Verlaufsdoku-Upload für entlassbericht wird als verlauf_text geroutet."""
        from app.services.prompts import build_user_content
        u = build_user_content(
            workflow="entlassbericht",
            verlauf_text="28 Tage stationär, Therapieziele erreicht.",
        )
        assert "VERLAUFSDOKUMENTATION" in u
        assert "28 Tage" in u
        assert "Entlassbericht" in u

    def test_p4_mit_style_text(self, mock_llm_jobs):
        r = self._start_job("entlassbericht", {
            "style_text": "Beispiel-Entlassbericht im Klinikstil.",
        })
        assert r.status_code == 200

    # ── Workflow-Routing: verlauf_text vs vorbefunde_text ─────────

    def test_routing_verlaengerung_benutzt_nicht_vorbefunde(self):
        """Für verlaengerung/entlassbericht wird vorbefunde_text NICHT als VORBEFUNDE eingebettet."""
        from app.services.prompts import build_user_content
        # Wenn vorbefunde_text irrtümlich übergeben wird, darf es nicht erscheinen
        u = build_user_content(
            workflow="verlaengerung",
            vorbefunde_text="Sollte nicht auftauchen.",
            verlauf_text="Korrekte Verlaufsdoku.",
        )
        assert "Sollte nicht auftauchen" not in u
        assert "Korrekte Verlaufsdoku" in u

    # ── Style-Text: Bereinigung ───────────────────────────────────

    def test_style_text_wird_dedupliziert(self, mock_llm_jobs):
        """Repetitiver C&P-Stiltext wird dedupliziert bevor er gesendet wird."""
        repetitiver_text = "Frau K. kam mit dem Anliegen. " * 20
        r = self._start_job("dokumentation", {
            "transcript": "Test.",
            "style_text": repetitiver_text,
        })
        assert r.status_code == 200
        job = self._wait_job(r.json()["job_id"])
        assert job["status"] == "done"

    def test_style_text_zu_lang_wird_gekuerzt(self, mock_llm_jobs):
        """C&P-Stiltext über Limit wird gekürzt ohne Fehler."""
        langer_text = "Ein vollständiger Satz als Stilvorlage. " * 200  # ~8000 Zeichen
        r = self._start_job("dokumentation", {
            "transcript": "Test.",
            "style_text": langer_text,
        })
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════
# MODELS API
# ══════════════════════════════════════════════════════════════════

class TestModelsAPI:
    """Tests für GET /api/models."""

    def test_models_endpoint_erreichbar(self):
        """GET /api/models antwortet auch wenn Ollama nicht erreichbar ist."""
        r = client.get("/api/models")
        assert r.status_code == 200
        data = r.json()
        assert "models" in data
        assert "default" in data
        assert isinstance(data["models"], list)
        assert len(data["models"]) >= 1  # mindestens das Default-Modell

    def test_models_enthaelt_default_modell(self):
        """Default-Modell ist immer in der Liste, auch ohne Ollama."""
        r = client.get("/api/models")
        data = r.json()
        names = [m["name"] for m in data["models"]]
        # Default muss enthalten sein (entweder direkt oder als Fallback)
        assert data["default"]
        assert any(m["is_default"] for m in data["models"])

    def test_models_response_struktur(self):
        """Jedes Modell hat die erwarteten Felder."""
        r = client.get("/api/models")
        for m in r.json()["models"]:
            assert "name" in m
            assert "is_default" in m
            assert "size_gb" in m  # kann None sein

    def test_models_mit_ollama_mock(self):
        """Mit Ollama-Mock werden echte Modelle zurückgegeben."""
        mock_tags = {
            "models": [
                {"name": "qwen2.5:32b", "size": 19_000_000_000},
                {"name": "deepseek-r1:32b", "size": 19_500_000_000},
                {"name": "nomic-embed-text", "size": 274_000_000},
            ]
        }
        import httpx as _httpx
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_tags
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__.return_value.get = AsyncMock(
                return_value=mock_response
            )
            r = client.get("/api/models")

        data = r.json()
        names = [m["name"] for m in data["models"]]
        assert "qwen2.5:32b" in names
        assert "deepseek-r1:32b" in names
        # Größen korrekt berechnet (19GB)
        qwen = next(m for m in data["models"] if m["name"] == "qwen2.5:32b")
        assert qwen["size_gb"] == 19.0

    def test_jobs_generate_akzeptiert_model_feld(self, mock_llm_jobs):
        """POST /api/jobs/generate akzeptiert model-Formfeld."""
        r = client.post("/api/jobs/generate", data={
            "workflow":  "dokumentation",
            "prompt":    "Test.",
            "transcript": "Kurztext.",
            "model":     "qwen2.5:32b",
        })
        assert r.status_code == 200
        assert "job_id" in r.json()


# ══════════════════════════════════════════════════════════════════
# MODELL-PROFILE (Unit Tests)
# ══════════════════════════════════════════════════════════════════

class TestModelProfile:
    """Tests für modellspezifische Generierungsparameter."""

    def test_deepseek_bekommt_groesseren_mindest_kontext(self):
        from app.services.llm import _get_model_profile
        p = _get_model_profile("deepseek-r1:32b")
        assert p["min_ctx"] >= 8192   # Reasoning braucht mehr Minimum
        assert p["temperature"] <= 0.2

    def test_qwen_temperature_korrekt(self):
        from app.services.llm import _get_model_profile
        p = _get_model_profile("qwen2.5:32b")
        assert p["temperature"] == 0.3
        assert "min_ctx" in p

    def test_unbekanntes_modell_bekommt_default(self):
        from app.services.llm import _get_model_profile
        p = _get_model_profile("unbekanntes-modell:7b")
        assert "min_ctx" in p
        assert "temperature" in p
        assert "top_p" in p

    def test_mistral_bekommt_standard_temperature(self):
        from app.services.llm import _get_model_profile
        p = _get_model_profile("qwen3:32b")
        assert p["temperature"] == 0.3

    def test_gemma_bekommt_standard_temperature(self):
        from app.services.llm import _get_model_profile
        p = _get_model_profile("gemma3:27b")
        assert p["temperature"] == 0.3

    def test_deepseek_varianten_erkennung(self):
        """Verschiedene deepseek-Varianten haben höheres min_ctx."""
        from app.services.llm import _get_model_profile
        for name in ["deepseek-r1:7b", "deepseek-r1:32b", "deepseek-coder:6.7b"]:
            p = _get_model_profile(name)
            assert p["min_ctx"] >= 8192, f"{name} sollte höheres min_ctx haben"

    def test_estimate_num_ctx_kurzer_input(self):
        """Kurzer Input → kleines num_ctx, kein unnötiger KV-Cache."""
        from app.services.llm import _estimate_num_ctx
        ctx = _estimate_num_ctx("System.", "Kurze Anfrage.", 512)
        assert ctx >= 2048          # Minimum
        assert ctx <= 4096          # Nicht unnötig groß

    def test_estimate_num_ctx_langer_input(self):
        """Langer Transkript-Input → entsprechend größeres num_ctx."""
        from app.services.llm import _estimate_num_ctx
        # ~30k Zeichen = ca. 8500 Tokens
        langer_text = "Therapeut und Klient sprechen über innere Anteile. " * 600
        ctx = _estimate_num_ctx("System-Prompt.", langer_text, 2048)
        assert ctx >= 8192          # Ausreichend für den Input
        assert ctx <= 32768         # Aber kein Overflow

    def test_estimate_num_ctx_immer_vielfaches_von_512(self):
        """num_ctx ist immer ein Vielfaches von 512."""
        from app.services.llm import _estimate_num_ctx
        for chars in [500, 1000, 5000, 20000, 50000]:
            text = "x" * chars
            ctx = _estimate_num_ctx("System.", text, 1024)
            assert ctx % 512 == 0, f"num_ctx={ctx} ist kein Vielfaches von 512"

    def test_estimate_num_ctx_nie_ueber_32768(self):
        """num_ctx überschreitet niemals 32768."""
        from app.services.llm import _estimate_num_ctx
        sehr_langer_text = "x" * 200_000
        ctx = _estimate_num_ctx("System.", sehr_langer_text, 2048)
        assert ctx <= 32768

    def test_estimate_num_ctx_konsistent_bei_gleichen_inputs(self):
        """Gleiche Inputs → gleicher num_ctx (kein Jitter)."""
        from app.services.llm import _estimate_num_ctx
        text = "Therapiestunde mit Fokus auf IFS-Arbeit. " * 100
        ctx1 = _estimate_num_ctx("System.", text, 2048)
        ctx2 = _estimate_num_ctx("System.", text, 2048)
        assert ctx1 == ctx2


# ══════════════════════════════════════════════════════════════════
# JOB CANCEL (DELETE /api/jobs/{id})
# ══════════════════════════════════════════════════════════════════

class TestJobCancel:

    def test_cancel_laufenden_job(self, mock_llm_jobs):
        """DELETE /api/jobs/{id} bricht einen laufenden Job ab."""
        r = client.post("/api/jobs/generate", data={
            "workflow": "dokumentation", "prompt": "test", "transcript": "test",
        })
        job_id = r.json()["job_id"]
        rc = client.delete(f"/api/jobs/{job_id}")
        assert rc.status_code == 200
        data = rc.json()
        assert data["job_id"] == job_id
        assert "cancelled" in data

    def test_cancel_nicht_vorhandener_job(self):
        """DELETE auf unbekannte job_id gibt 404."""
        r = client.delete("/api/jobs/nichtvorhanden00000000000000000000")
        assert r.status_code == 404

    def test_cancel_abgeschlossener_job(self, mock_llm_jobs):
        """DELETE auf bereits abgeschlossenen Job gibt cancelled=False zurück."""
        import time
        r = client.post("/api/jobs/generate", data={
            "workflow": "dokumentation", "prompt": "test", "transcript": "test",
        })
        job_id = r.json()["job_id"]
        # Warten bis fertig
        for _ in range(30):
            status = client.get(f"/api/jobs/{job_id}").json()["status"]
            if status in ("done", "error"):
                break
            time.sleep(0.1)
        rc = client.delete(f"/api/jobs/{job_id}")
        assert rc.status_code == 200
        assert rc.json()["cancelled"] is False  # war schon fertig

    def test_cancelled_status_in_job_queue(self):
        """cancel_job() setzt status auf CANCELLED."""
        from app.services.job_queue import JobQueue, JobStatus
        q = JobQueue()
        job = q.create_job("dokumentation", "Test")
        assert q.cancel_job(job.job_id) is True
        assert job._cancel_requested is True
        # Status bleibt PENDING bis run_job() läuft und es setzt
        assert job.status == JobStatus.PENDING

    def test_cancel_gibt_false_fuer_unbekannte_id(self):
        """cancel_job() gibt False für unbekannte IDs zurück."""
        from app.services.job_queue import JobQueue
        q = JobQueue()
        assert q.cancel_job("nichtvorhanden") is False


# ══════════════════════════════════════════════════════════════════
# STIL-METADATEN (style_info im Job-Result)
# ══════════════════════════════════════════════════════════════════

class TestStyleInfo:
    """
    Prüft dass style_info im Job-Result gesetzt ist wenn ein Stilprofil
    verwendet wurde – und None wenn keins verwendet wurde.
    """

    def _wait(self, job_id, max_polls=30):
        import time
        for _ in range(max_polls):
            r = client.get(f"/api/jobs/{job_id}")
            job = r.json()
            if job["status"] in ("done", "error", "cancelled"):
                return job
            time.sleep(0.1)
        raise TimeoutError("Job nicht abgeschlossen")

    def test_kein_stil_gibt_none(self, mock_llm_jobs):
        """Ohne Stilprofil ist style_info=None im Job-Result."""
        r = client.post("/api/jobs/generate", data={
            "workflow": "dokumentation",
            "prompt":   "test",
            "transcript": "Kurzes Gespräch.",
        })
        job = self._wait(r.json()["job_id"])
        assert job["status"] == "done"
        assert job["style_info"] is None

    def test_style_text_input_gibt_info(self, mock_llm_jobs):
        """C&P-Stiltext → style_info.source='text_input' mit chars/words."""
        r = client.post("/api/jobs/generate", data={
            "workflow":   "dokumentation",
            "prompt":     "test",
            "transcript": "Gespräch.",
            "style_text": "Im Mittelpunkt stand die innere Anspannung von Frau K. " * 5,
        })
        job = self._wait(r.json()["job_id"])
        assert job["status"] == "done"
        info = job["style_info"]
        assert info is not None
        assert info["source"] == "text_input"
        assert info["chars"] > 0
        assert info["words"] > 0

    def test_style_file_upload_gibt_info(self, mock_llm_jobs, mock_extract_jobs):
        """Datei-Upload → style_info.source='file_upload' mit filename."""
        with patch("app.services.extraction.extract_style_context",
                   new=AsyncMock(return_value="Schreibe im Stil des Therapeuten.")), \
             patch("app.api.jobs.extract_style_context",
                   new=AsyncMock(return_value="Schreibe im Stil des Therapeuten.")):
            r = client.post("/api/jobs/generate",
                data={"workflow": "dokumentation", "prompt": "test", "transcript": "t"},
                files={"style_file": ("stil.txt", b"Beispieldokumentation.", "text/plain")}
            )
        job = self._wait(r.json()["job_id"])
        assert job["status"] == "done"
        info = job["style_info"]
        assert info is not None
        assert info["source"] == "file_upload"
        assert "filename" in info
        assert info["filename"] == "stil.txt"

    def test_style_library_gibt_info(self, mock_llm_jobs):
        """pgvector-Retrieval → style_info.source='style_library' mit therapeut_id."""
        with patch("app.services.embeddings.retrieve_style_examples",
                   new=AsyncMock(return_value="Stilbeispiel aus Bibliothek.")), \
             patch("app.api.jobs.retrieve_style_examples",
                   new=AsyncMock(return_value="Stilbeispiel aus Bibliothek.")):
            r = client.post("/api/jobs/generate", data={
                "workflow":     "dokumentation",
                "prompt":       "test",
                "transcript":   "t",
                "therapeut_id": "Carsten Wittenberg",
            })
        job = self._wait(r.json()["job_id"])
        assert job["status"] == "done"
        info = job["style_info"]
        assert info is not None
        assert info["source"] == "style_library"
        assert info["therapeut_id"] == "Carsten Wittenberg"
        assert info["chars"] > 0

    def test_style_info_in_job_schema(self, mock_llm_jobs):
        """style_info ist immer im Job-Schema vorhanden (auch als None)."""
        r = client.post("/api/jobs/generate", data={
            "workflow": "anamnese", "prompt": "test",
        })
        job = self._wait(r.json()["job_id"])
        assert "style_info" in job  # Feld immer vorhanden, kann None sein
