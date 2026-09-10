"""
tests/integration/test_jobs_multi.py
────────────────────────────────────
Sprint B: Multi-Job-Liste fuer P1 (Gespraechsdokumentation).

Abgedeckte Akzeptanzkriterien:

  B1: GET /api/jobs filterbar
      - workflow-Query-Param filtert korrekt
      - unbekannter Workflow → 422
      - isoliert nach therapeut_id (aus Auth-Header, nicht Form)
      - sortiert neueste zuerst
      - limit ausserhalb [1,500] → 422 (FastAPI Query-Validierung)

  B2: patient_kuerzel persistiert
      - Form-Param "patientenname" landet als patient_kuerzel im Job-Dict
      - ohne Form-Param: patient_kuerzel = None

  B3: DELETE /api/jobs/{id}/permanent
      - done-Job → 200, GET danach 404, weg aus Liste
      - pending/running-Job → 409
      - unbekannte ID → 404

  B4: Persistenz nach Cache-Clear
      - Job ueberlebt job_queue._cache.clear() (Quelle: DB)

Setup:
  - Nutzt die autouse-Fixtures aus tests/integration/conftest.py
    (init_test_db, _disable_pgvector_for_sqlite, _reset_ollama_client).
  - Importiert KEINE Fixtures aus tests/conftest.py (existiert im Repo
    nicht vollstaendig; Tests muessen self-contained sein).
  - Auth-Override am Modul-Anfang wie in test_api.py.
"""
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.auth import get_current_user as _get_current_user


# ── Auth-Override fuer alle Tests in diesem Modul ──────────────────
# Default: "test-therapeut". Isolation-Test ueberschreibt temporaer.

async def _default_user() -> str:
    return "test-therapeut"


app.dependency_overrides[_get_current_user] = _default_user

client = TestClient(app)


# ── Fixtures ──────────────────────────────────────────────────────

_MOCK_RESPONSE = {
    "text":       "Generierter Text fuer Test.",
    "model_used": "ollama/qwen3:32b",
    "duration_s": 0.1,
    "token_count": 20,
}


@pytest.fixture
def mock_llm_jobs():
    """LLM-Mock - gleicher Patch-Pfad wie in test_api.py::mock_llm_jobs."""
    with patch("app.services.generation_pipeline.generate_text",     new=AsyncMock(return_value=_MOCK_RESPONSE)), \
         patch("app.services.llm.generate_text", new=AsyncMock(return_value=_MOCK_RESPONSE)):
        yield


@pytest.fixture(autouse=True)
def _clear_job_cache():
    """Cache vor/nach jedem Test leeren - sonst sehen wir Reste aus Vortests.

    init_test_db (autouse aus integration/conftest.py) droppt die DB-Tabellen
    aber NICHT den modulweiten job_queue._cache. Ohne diese Fixture wuerden
    Tests sich gegenseitig verschmutzen.
    """
    from app.services.job_queue import job_queue
    job_queue._cache.clear()
    yield
    job_queue._cache.clear()


# ── Helfer ────────────────────────────────────────────────────────

def _start(workflow="dokumentation", extra=None):
    """Startet einen Job ueber den HTTP-Endpoint und liefert die job_id."""
    data = {
        "workflow":              workflow,
        "workflow_instructions": f"Test-Anweisungen fuer {workflow}",
    }
    # Mindest-Inputs pro Workflow damit der Pipeline-Validator nicht meckert
    if workflow == "dokumentation":
        data.setdefault("transcript", "Patient berichtet von Fortschritten.")
    elif workflow == "anamnese":
        data.setdefault("diagnosen", "F32.1")
    if extra:
        data.update(extra)
    r = client.post("/api/jobs/generate", data=data)
    assert r.status_code == 200, f"create_job fehlgeschlagen: {r.status_code} {r.text}"
    return r.json()["job_id"]


def _wait(job_id, max_polls=40):
    """Wartet bis Job terminal ist (done/error/cancelled).

    Status wird in JobState gesetzt BEVOR _persist_job() im finally-Block der
    DB-Write durchfuehrt - wir warten daher noch kurz nach Status-Change damit
    die DB-Zeile garantiert geschrieben ist (wichtig fuer B4-Test).
    """
    for _ in range(max_polls):
        r = client.get(f"/api/jobs/{job_id}")
        if r.status_code == 404:
            return None
        if r.json()["status"] in ("done", "error", "cancelled"):
            time.sleep(0.25)  # _persist_job nachlaufen lassen
            return r.json()
        time.sleep(0.1)
    raise TimeoutError(f"Job {job_id} nicht abgeschlossen")


# ══════════════════════════════════════════════════════════════════
# B1 – GET /api/jobs filterbar
# ══════════════════════════════════════════════════════════════════

class TestJobsListFilter:

    def test_jobs_filter_by_workflow(self, mock_llm_jobs):
        """?workflow=dokumentation liefert nur dokumentation-Jobs."""
        doko_ids = [_start("dokumentation") for _ in range(3)]
        for jid in doko_ids:
            _wait(jid)
        # 2 Jobs eines anderen Workflows
        anam_ids = [_start("anamnese") for _ in range(2)]
        for jid in anam_ids:
            _wait(jid)

        r = client.get("/api/jobs?workflow=dokumentation")
        assert r.status_code == 200
        jobs = r.json()
        assert all(j["workflow"] == "dokumentation" for j in jobs)
        ids_in_list = [j["job_id"] for j in jobs]
        for jid in doko_ids:
            assert jid in ids_in_list, f"erwarteter Doku-Job {jid} fehlt in Liste"
        for jid in anam_ids:
            assert jid not in ids_in_list, f"Anamnese-Job {jid} sollte nicht in Doku-Liste sein"

    def test_jobs_filter_unbekannter_workflow_422(self):
        """Unbekannter Workflow → 422 (WorkflowLiteral-Validierung)."""
        r = client.get("/api/jobs?workflow=blubb")
        assert r.status_code == 422

    def test_jobs_isolation_by_therapeut(self, mock_llm_jobs):
        """Therapeut A sieht keine Jobs von Therapeut B."""
        async def fake_a():
            return "user-a"
        async def fake_b():
            return "user-b"

        try:
            # Job als A anlegen + abwarten
            app.dependency_overrides[_get_current_user] = fake_a
            job_a = _start("dokumentation")
            _wait(job_a)

            # Job als B anlegen + abwarten
            app.dependency_overrides[_get_current_user] = fake_b
            job_b = _start("dokumentation")
            _wait(job_b)

            # A sieht nur eigene Jobs
            app.dependency_overrides[_get_current_user] = fake_a
            r = client.get("/api/jobs?workflow=dokumentation")
            ids = [j["job_id"] for j in r.json()]
            assert job_a in ids
            assert job_b not in ids

            # B sieht nur eigene Jobs
            app.dependency_overrides[_get_current_user] = fake_b
            r = client.get("/api/jobs?workflow=dokumentation")
            ids = [j["job_id"] for j in r.json()]
            assert job_b in ids
            assert job_a not in ids
        finally:
            # Default wiederherstellen (test-therapeut)
            app.dependency_overrides[_get_current_user] = _default_user

    def test_jobs_sortierung_neueste_zuerst(self, mock_llm_jobs):
        """Liste ist nach created_at DESC sortiert (neueste zuerst)."""
        ids = []
        for _ in range(3):
            ids.append(_start("dokumentation"))
            time.sleep(0.02)  # eindeutige created_at-Werte erzwingen
        for jid in ids:
            _wait(jid)

        r = client.get("/api/jobs?workflow=dokumentation")
        jobs_in_test = [j for j in r.json() if j["job_id"] in ids]
        # Der zuletzt erstellte muss als erster in der Liste stehen
        assert jobs_in_test[0]["job_id"] == ids[-1]
        assert jobs_in_test[-1]["job_id"] == ids[0]

    def test_jobs_limit_grenzen_422(self):
        """Limit ausserhalb [1,500] → 422 von FastAPI Query-Validierung."""
        assert client.get("/api/jobs?limit=0").status_code     == 422
        assert client.get("/api/jobs?limit=99999").status_code == 422
        assert client.get("/api/jobs?limit=1").status_code     == 200
        assert client.get("/api/jobs?limit=500").status_code   == 200


# ══════════════════════════════════════════════════════════════════
# B2 – patient_kuerzel persistiert
# ══════════════════════════════════════════════════════════════════

class TestPatientKuerzel:

    def test_patient_kuerzel_persistiert(self, mock_llm_jobs):
        """patientenname → patient_kuerzel in Single-GET UND in Liste."""
        job_id = _start("dokumentation", extra={"patientenname": "Frau M."})
        _wait(job_id)

        single = client.get(f"/api/jobs/{job_id}").json()
        assert single.get("patient_kuerzel") == "Frau M."

        lst = client.get("/api/jobs?workflow=dokumentation").json()
        match = next((j for j in lst if j["job_id"] == job_id), None)
        assert match is not None
        assert match["patient_kuerzel"] == "Frau M."

    def test_ohne_patientenname_ist_kuerzel_none(self, mock_llm_jobs):
        """Ohne Form-Param patientenname → patient_kuerzel = None."""
        job_id = _start("dokumentation")
        _wait(job_id)

        single = client.get(f"/api/jobs/{job_id}").json()
        assert single.get("patient_kuerzel") is None


# ══════════════════════════════════════════════════════════════════
# B3 – DELETE /api/jobs/{id}/permanent
# ══════════════════════════════════════════════════════════════════

class TestDeletePermanent:

    def test_delete_permanent_done_job(self, mock_llm_jobs):
        """Done-Job laesst sich permanent loeschen, danach 404 + weg aus Liste."""
        job_id = _start("dokumentation")
        _wait(job_id)

        r = client.delete(f"/api/jobs/{job_id}/permanent")
        assert r.status_code == 200
        body = r.json()
        assert body["deleted"] is True
        assert body["job_id"] == job_id

        # Nicht mehr abrufbar
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
        # Nicht mehr in der Liste
        lst = client.get("/api/jobs?workflow=dokumentation").json()
        assert job_id not in [j["job_id"] for j in lst]

    def test_delete_permanent_pending_job_blocked(self):
        """Pending-Job permanent loeschen → 409 (Cancel-Hinweis im Detail)."""
        from app.services.job_queue import job_queue
        # Job direkt im Cache anlegen ohne run_job() - bleibt im pending-State.
        # Synchron, _db_insert_job laeuft fire-and-forget; das ist ok weil
        # delete_job_permanent zuerst den Cache prueft.
        job = job_queue.create_job(
            "dokumentation",
            description="test-pending",
            therapeut_id="test-therapeut",
        )
        assert job.status == "pending"

        r = client.delete(f"/api/jobs/{job.job_id}/permanent")
        assert r.status_code == 409
        assert "abbrechen" in r.json().get("detail", "").lower()

    def test_delete_permanent_unbekannte_id(self):
        """Unbekannte ID → 404."""
        r = client.delete("/api/jobs/nichtvorhandene000000000000000000/permanent")
        assert r.status_code == 404


# ══════════════════════════════════════════════════════════════════
# B4 – Persistenz nach Cache-Clear
# ══════════════════════════════════════════════════════════════════

class TestPersistenzNachCacheClear:

    def test_job_ueberlebt_cache_clear(self, mock_llm_jobs):
        """Nach _cache.clear() ist der Job weiterhin in der Liste (DB-Quelle)."""
        from app.services.job_queue import job_queue

        job_id = _start("dokumentation", extra={"patientenname": "Herr S."})
        _wait(job_id)

        # Cache explizit leeren (simuliert uvicorn-Restart oder Cache-Eviction)
        job_queue._cache.clear()

        lst = client.get("/api/jobs?workflow=dokumentation").json()
        ids = [j["job_id"] for j in lst]
        assert job_id in ids

        job = next(j for j in lst if j["job_id"] == job_id)
        assert job["status"]          == "done"
        assert job["workflow"]        == "dokumentation"
        assert job["patient_kuerzel"] == "Herr S."
        assert job["result_text"]
        assert job["created_at"]
        assert job["finished_at"]
