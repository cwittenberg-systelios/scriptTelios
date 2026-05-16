"""
Tests fuer den Admin-Endpoint (/api/admin/whisper-model).

Testet:
- GET liefert aktuelles Modell
- POST mit gueltigem Modell wechselt
- POST mit ungueltigem Modell gibt 400
- Auth: ohne X-Admin-Token bei AUTH_ENABLED=true gibt 401

Diese Tests benutzen FastAPI TestClient (kein laufender Server noetig).
"""
import pytest
from unittest.mock import patch

from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """FastAPI TestClient mit isolierter App-Instanz."""
    from app.main import app
    return TestClient(app)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/admin/whisper-model
# ─────────────────────────────────────────────────────────────────────────────


class TestGetWhisperModel:

    def test_get_liefert_aktuelles_modell(self, client):
        response = client.get("/api/admin/whisper-model")
        assert response.status_code == 200
        data = response.json()
        assert "whisper_model" in data
        assert "whisper_device" in data
        assert "whisper_compute_type" in data

    def test_get_braucht_keine_auth(self, client):
        # GET ist ein read-only Status-Endpoint, sollte ohne Auth gehen
        response = client.get("/api/admin/whisper-model")
        assert response.status_code != 401


# ─────────────────────────────────────────────────────────────────────────────
# POST /api/admin/whisper-model
# ─────────────────────────────────────────────────────────────────────────────


class TestPostWhisperModel:

    def test_gueltiges_modell_wird_akzeptiert(self, client):
        # AUTH_ENABLED=false als Default in dev → kein Token noetig
        with patch("app.api.admin.settings") as mock_settings:
            mock_settings.AUTH_ENABLED = False
            mock_settings.WHISPER_MODEL = "large-v3"
            mock_settings.CONFLUENCE_SHARED_SECRET = ""

            response = client.post("/api/admin/whisper-model?model=medium")
            assert response.status_code == 200
            data = response.json()
            assert data["ok"] is True
            assert data["whisper_model"] == "medium"

    def test_ungueltiges_modell_gibt_400(self, client):
        with patch("app.api.admin.settings") as mock_settings:
            mock_settings.AUTH_ENABLED = False
            mock_settings.WHISPER_MODEL = "large-v3"

            response = client.post("/api/admin/whisper-model?model=invalid-model-xyz")
            assert response.status_code == 400
            assert "Unbekanntes" in response.json().get("detail", "")

    def test_auth_enabled_ohne_token_gibt_401(self, client):
        with patch("app.api.admin.settings") as mock_settings:
            mock_settings.AUTH_ENABLED = True
            mock_settings.CONFLUENCE_SHARED_SECRET = "supersecret"
            mock_settings.WHISPER_MODEL = "large-v3"

            response = client.post("/api/admin/whisper-model?model=medium")
            assert response.status_code == 401

    def test_auth_enabled_mit_falschem_token_gibt_401(self, client):
        with patch("app.api.admin.settings") as mock_settings:
            mock_settings.AUTH_ENABLED = True
            mock_settings.CONFLUENCE_SHARED_SECRET = "supersecret"
            mock_settings.WHISPER_MODEL = "large-v3"

            response = client.post(
                "/api/admin/whisper-model?model=medium",
                headers={"X-Admin-Token": "falsch"},
            )
            assert response.status_code == 401

    def test_auth_enabled_mit_korrektem_token_geht_durch(self, client):
        with patch("app.api.admin.settings") as mock_settings:
            mock_settings.AUTH_ENABLED = True
            mock_settings.CONFLUENCE_SHARED_SECRET = "supersecret"
            mock_settings.WHISPER_MODEL = "large-v3"

            response = client.post(
                "/api/admin/whisper-model?model=medium",
                headers={"X-Admin-Token": "supersecret"},
            )
            assert response.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Modell-Validierung
# ─────────────────────────────────────────────────────────────────────────────


class TestValidModels:
    """Stellt sicher dass die VALID_MODELS-Liste alle relevanten Modelle enthaelt."""

    def test_alle_relevanten_modelle_sind_erlaubt(self):
        from app.api.admin import VALID_MODELS
        # Diese Modelle muessen in der Liste sein:
        required = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
        for m in required:
            assert m in VALID_MODELS, f"{m} fehlt in VALID_MODELS"

    def test_distil_modelle_sind_erlaubt(self):
        from app.api.admin import VALID_MODELS
        # Distil-Versionen sind moderne schnelle Alternativen
        assert "distil-large-v3" in VALID_MODELS
