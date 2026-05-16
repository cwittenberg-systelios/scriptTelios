"""
Tests fuer app/middleware/audit.py.

Schwerpunkte:
  - Skip-Pfade werden nicht geloggt
  - OPTIONS-Preflights laufen ohne Logging durch
  - Job-ID wird aus Pfaden /jobs/{id} extrahiert
  - status_code + duration_ms landen im Log
"""
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware.audit import AuditMiddleware, _audit_logger


@pytest.fixture
def audit_log_path(tmp_path, monkeypatch):
    """Audit-Logger auf eine Test-Datei umleiten."""
    log_file = tmp_path / "audit.log"

    # Bestehende Handler entfernen + neuen FileHandler auf tmp_path setzen
    import logging
    for h in list(_audit_logger.handlers):
        _audit_logger.removeHandler(h)

    handler = logging.FileHandler(str(log_file), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(handler)
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False

    yield log_file

    # Cleanup
    for h in list(_audit_logger.handlers):
        _audit_logger.removeHandler(h)


@pytest.fixture
def test_app():
    """Minimale FastAPI-App mit AuditMiddleware."""
    app = FastAPI()
    app.add_middleware(AuditMiddleware)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        return {"job_id": job_id}

    @app.post("/api/jobs/generate")
    async def post_job():
        return {"id": "abc123"}

    @app.get("/api/foo")
    async def foo():
        return {"foo": "bar"}

    return app


def _read_audit_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class TestSkipPaths:

    def test_health_wird_nicht_geloggt(self, test_app, audit_log_path):
        client = TestClient(test_app)
        r = client.get("/api/health")
        assert r.status_code == 200
        lines = _read_audit_lines(audit_log_path)
        assert not any(l["path"] == "/api/health" for l in lines)

    def test_normale_route_wird_geloggt(self, test_app, audit_log_path):
        client = TestClient(test_app)
        r = client.get("/api/foo")
        assert r.status_code == 200
        lines = _read_audit_lines(audit_log_path)
        assert any(l["path"] == "/api/foo" for l in lines)


class TestOptionsPreflight:

    def test_options_request_wird_nicht_geloggt(self, test_app, audit_log_path):
        """OPTIONS-Preflight (CORS) soll Audit ueberspringen."""
        client = TestClient(test_app)
        # FastAPI/TestClient antwortet auf OPTIONS evtl. mit 405; das ist OK,
        # entscheidend ist nur dass die Middleware ihn skippt.
        r = client.options("/api/foo")
        lines = _read_audit_lines(audit_log_path)
        # Kein Eintrag mit method=OPTIONS
        assert not any(l.get("method") == "OPTIONS" for l in lines)


class TestJobIdExtraktion:

    def test_job_id_aus_pfad_extrahiert(self, test_app, audit_log_path):
        job_uuid = "abc123def456789012345678901234ab"
        client = TestClient(test_app)
        r = client.get(f"/api/jobs/{job_uuid}")
        assert r.status_code == 200

        lines = _read_audit_lines(audit_log_path)
        job_entries = [l for l in lines if l["path"].startswith("/api/jobs/")]
        assert len(job_entries) == 1
        assert job_entries[0]["job_id"] == job_uuid[:36]

    def test_job_generate_pfad_extrahiert_generate_als_id(self, test_app, audit_log_path):
        """Erwartetes Verhalten der aktuellen Implementierung: alles nach /jobs/
        wird als id geparst — fuer /jobs/generate landet "generate" als job_id.
        Das ist ein bekannter quirk, kein Bug. Dieser Test dokumentiert ihn."""
        client = TestClient(test_app)
        r = client.post("/api/jobs/generate")
        assert r.status_code == 200

        lines = _read_audit_lines(audit_log_path)
        gen_entries = [l for l in lines if l["path"] == "/api/jobs/generate"]
        assert gen_entries
        # job_id wird zu "generate" (truncated auf 36 chars)
        assert gen_entries[0]["job_id"] == "generate"


class TestEntryFelder:

    def test_alle_pflicht_felder_vorhanden(self, test_app, audit_log_path):
        client = TestClient(test_app)
        r = client.get("/api/foo")
        assert r.status_code == 200
        lines = _read_audit_lines(audit_log_path)
        assert lines
        entry = lines[-1]
        for field in ("ts", "user", "method", "path", "status", "duration_ms", "ip"):
            assert field in entry, f"Pflichtfeld '{field}' fehlt im Audit-Entry"

    def test_status_code_im_log(self, test_app, audit_log_path):
        client = TestClient(test_app)
        client.get("/api/foo")
        lines = _read_audit_lines(audit_log_path)
        assert lines[-1]["status"] == 200

    def test_user_default_minus(self, test_app, audit_log_path):
        """Ohne Auth-Header: user == '-'."""
        client = TestClient(test_app)
        client.get("/api/foo")
        lines = _read_audit_lines(audit_log_path)
        assert lines[-1]["user"] == "-"

    def test_ts_ist_unix_int(self, test_app, audit_log_path):
        """audit.py schreibt int(time.time()) – nicht ISO. Sonst greift retention.py
        anders. Dies ist ein Regressions-Test fuer den ISO-Bug-Fix."""
        client = TestClient(test_app)
        client.get("/api/foo")
        lines = _read_audit_lines(audit_log_path)
        ts = lines[-1]["ts"]
        assert isinstance(ts, int), f"audit-ts soll int sein, ist aber {type(ts)}"

    def test_duration_ms_plausibel(self, test_app, audit_log_path):
        client = TestClient(test_app)
        client.get("/api/foo")
        lines = _read_audit_lines(audit_log_path)
        dur = lines[-1]["duration_ms"]
        assert isinstance(dur, int) and dur >= 0
