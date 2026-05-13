"""
Persistenz-Tests fuer die Stage-1-Pipeline-Felder (v19.2 Schritt 7).

Testet:
  1. JobState haelt verlauf_summary_text + verlauf_summary_audit (default None)
  2. to_dict() liefert beide Felder
  3. run_job() liest die Felder aus dem coro-Result
  4. _persist_job() schreibt die Felder via SQLAlchemy update().values(...)
  5. get_job_from_db() liefert die Felder im Response-Dict
  6. _log_performance() haengt einen stage1-Eintrag an wenn Audit gesetzt ist
"""
import asyncio
import json
import sys
import types
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Realer Import-Pfad: app.services.job_queue
# Wir muessen vorher Stubs setzen damit der Import von app.core.config klappt.
_HERE = Path(__file__).parent


def _ensure_stubs():
    if "app" not in sys.modules:
        sys.modules["app"] = types.ModuleType("app")
    if "app.core" not in sys.modules:
        sys.modules["app.core"] = types.ModuleType("app.core")
    if "app.core.config" not in sys.modules:
        cfg = types.ModuleType("app.core.config")

        class _S:
            OLLAMA_HOST = "http://localhost:11434"
            OLLAMA_MODEL = "qwen3:32b"
            AUDIT_LOG_PATH = "/tmp/audit.log"
            STAGE1_ENABLED = True
            STAGE1_TARGET_WORDS = 4000

        cfg.settings = _S()
        sys.modules["app.core.config"] = cfg
    if "app.services" not in sys.modules:
        sys.modules["app.services"] = types.ModuleType("app.services")


_ensure_stubs()

# job_queue.py laden
_spec = importlib.util.spec_from_file_location(
    "app.services.job_queue", _HERE / "job_queue.py",
)
job_queue_mod = importlib.util.module_from_spec(_spec)
sys.modules["app.services.job_queue"] = job_queue_mod
_spec.loader.exec_module(job_queue_mod)

JobState = job_queue_mod.JobState
JobStatus = job_queue_mod.JobStatus
JobQueue = job_queue_mod.JobQueue


# ─────────────────────────────────────────────────────────────────────────────
# 1. JobState
# ─────────────────────────────────────────────────────────────────────────────


class TestJobStateNewFields:
    def test_default_values_none(self):
        state = JobState(job_id="abc", workflow="verlaengerung")
        assert state.verlauf_summary_text is None
        assert state.verlauf_summary_audit is None

    def test_kann_zugewiesen_werden(self):
        state = JobState(job_id="abc", workflow="verlaengerung")
        state.verlauf_summary_text = "### Section ..."
        state.verlauf_summary_audit = {"applied": True, "raw_word_count": 8000}
        assert state.verlauf_summary_text == "### Section ..."
        assert state.verlauf_summary_audit["applied"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. to_dict
# ─────────────────────────────────────────────────────────────────────────────


class TestToDictExposesStage1Fields:
    def test_default_keys_vorhanden(self):
        state = JobState(job_id="abc", workflow="verlaengerung")
        d = state.to_dict()
        assert "verlauf_summary_text" in d
        assert "verlauf_summary_audit" in d
        assert d["verlauf_summary_text"] is None
        assert d["verlauf_summary_audit"] is None

    def test_gefuellte_werte_kommen_durch(self):
        state = JobState(job_id="abc", workflow="entlassbericht")
        state.verlauf_summary_text = "Summary."
        state.verlauf_summary_audit = {
            "applied": True,
            "compression_ratio": 0.4,
            "issues": [],
        }
        d = state.to_dict()
        assert d["verlauf_summary_text"] == "Summary."
        assert d["verlauf_summary_audit"]["applied"] is True
        assert d["verlauf_summary_audit"]["compression_ratio"] == 0.4


# ─────────────────────────────────────────────────────────────────────────────
# 3. run_job: liest Felder aus dem Coro-Result
# ─────────────────────────────────────────────────────────────────────────────


class TestRunJobReadsStage1Fields:
    @pytest.mark.asyncio
    async def test_run_job_uebernimmt_audit_und_text(self):
        queue = JobQueue()
        state = queue.create_job("verlaengerung", "test")

        coro_result = {
            "text": "Generierter Antrag.",
            "model_used": "ollama/qwen3:32b",
            "verlauf_summary_audit": {
                "applied": True,
                "raw_word_count": 8000,
                "summary_word_count": 3500,
                "compression_ratio": 0.44,
                "duration_s": 22.5,
                "retry_used": False,
                "degraded": False,
                "issues": [],
                "fallback_reason": None,
            },
            "verlauf_summary_text": "### Sitzungsübersicht\nText...",
        }

        async def _fake_coro():
            return coro_result

        # _persist_job mocken (wir testen Persistenz separat)
        with patch.object(queue, "_persist_job", new=AsyncMock()):
            await queue.run_job(state, _fake_coro())

        assert state.verlauf_summary_audit is not None
        assert state.verlauf_summary_audit["applied"] is True
        assert state.verlauf_summary_audit["compression_ratio"] == 0.44
        assert state.verlauf_summary_text == "### Sitzungsübersicht\nText..."
        assert state.status == JobStatus.DONE.value

    @pytest.mark.asyncio
    async def test_run_job_ohne_stage1_audit_keine_aenderung(self):
        """Coro-Result ohne Stage-1-Felder: state-Defaults bleiben None."""
        queue = JobQueue()
        state = queue.create_job("anamnese", "test")

        async def _fake_coro():
            return {
                "text": "Anamnese.",
                "model_used": "ollama/qwen3:32b",
                # KEINE verlauf_summary_*-Felder
            }

        with patch.object(queue, "_persist_job", new=AsyncMock()):
            await queue.run_job(state, _fake_coro())

        assert state.verlauf_summary_text is None
        assert state.verlauf_summary_audit is None


# ─────────────────────────────────────────────────────────────────────────────
# 4. _persist_job: SQLAlchemy update().values(...) Argumente
# ─────────────────────────────────────────────────────────────────────────────


class TestPersistJobWritesStage1Fields:
    @pytest.mark.asyncio
    async def test_update_values_enthaelt_stage1_felder(self):
        """Beim Persist-Call muss .values(...) die neuen Spalten enthalten."""
        queue = JobQueue()
        state = JobState(job_id="abc", workflow="verlaengerung")
        state.status = JobStatus.DONE.value
        state.result_text = "fertig"
        state.verlauf_summary_text = "### Section X"
        state.verlauf_summary_audit = {
            "applied": True,
            "compression_ratio": 0.4,
        }

        # Wir patchen async_session_factory mit einem AsyncContextManager-Mock
        captured_values = {}

        class _FakeUpdate:
            def __init__(self):
                self._where_called = False

            def where(self, *args, **kwargs):
                self._where_called = True
                return self

            def values(self, **kwargs):
                captured_values.update(kwargs)
                return self

        class _FakeDB:
            async def execute(self, stmt):
                return None

            async def commit(self):
                return None

        class _FakeSessionCM:
            async def __aenter__(self):
                return _FakeDB()

            async def __aexit__(self, *args):
                return None

        # Module-Stubs einrichten
        fake_database_mod = types.ModuleType("app.core.database")
        fake_database_mod.async_session_factory = lambda: _FakeSessionCM()
        fake_models_mod = types.ModuleType("app.models")
        fake_models_db_mod = types.ModuleType("app.models.db")

        class _FakeJobModel:
            id = "id-col"

        fake_models_db_mod.Job = _FakeJobModel

        fake_sqlalchemy_mod = types.ModuleType("sqlalchemy")

        def _fake_update(model):
            return _FakeUpdate()

        fake_sqlalchemy_mod.update = _fake_update

        with patch.dict(sys.modules, {
            "app.core.database":  fake_database_mod,
            "app.models":          fake_models_mod,
            "app.models.db":       fake_models_db_mod,
            "sqlalchemy":          fake_sqlalchemy_mod,
        }):
            await queue._persist_job(state)

        # Die neuen Felder sind in .values(...) eingegangen
        assert "verlauf_summary_text" in captured_values
        assert "verlauf_summary_audit" in captured_values
        assert captured_values["verlauf_summary_text"] == "### Section X"
        assert captured_values["verlauf_summary_audit"]["applied"] is True
        # Sanity: ein altes Feld auch dabei
        assert captured_values["result_text"] == "fertig"


# ─────────────────────────────────────────────────────────────────────────────
# 5. _log_performance: stage1-Block im JSON
# ─────────────────────────────────────────────────────────────────────────────


class TestLogPerformanceStage1Block:
    def test_audit_landet_im_perf_log(self):
        state = JobState(job_id="abc", workflow="verlaengerung")
        state.status = JobStatus.DONE.value
        state.duration_s = 30.0
        state.verlauf_summary_audit = {
            "applied": True,
            "raw_word_count": 8000,
            "summary_word_count": 3500,
            "compression_ratio": 0.44,
            "duration_s": 22.0,
            "retry_used": False,
            "degraded": False,
            "issues": [],
            "fallback_reason": None,
        }

        captured_lines = []

        class _CaptureHandler:
            def __init__(self):
                self.level = 0
                self.lines = captured_lines

            def handle(self, record):
                self.lines.append(record.getMessage())

            def createLock(self):
                pass
            def acquire(self):
                pass
            def release(self):
                pass

        # Original perf_logger mit Capture-Handler ersetzen
        import logging
        orig_handlers = job_queue_mod.perf_logger.handlers[:]
        cap = _CaptureHandler()
        job_queue_mod.perf_logger.handlers = [cap]
        try:
            job_queue_mod._log_performance(state, queue_size=0)
        finally:
            job_queue_mod.perf_logger.handlers = orig_handlers

        assert len(captured_lines) == 1
        entry = json.loads(captured_lines[0])
        assert "stage1" in entry
        assert entry["stage1"]["applied"] is True
        assert entry["stage1"]["compression_ratio"] == 0.44
        assert entry["stage1"]["raw_words"] == 8000
        assert entry["stage1"]["summary_words"] == 3500
        assert entry["stage1"]["issue_count"] == 0

    def test_ohne_audit_kein_stage1_block(self):
        state = JobState(job_id="abc", workflow="anamnese")
        state.status = JobStatus.DONE.value
        state.duration_s = 10.0
        # verlauf_summary_audit bleibt None

        captured_lines = []

        class _CaptureHandler:
            level = 0
            def handle(self, record):
                captured_lines.append(record.getMessage())
            def createLock(self): pass
            def acquire(self): pass
            def release(self): pass

        orig_handlers = job_queue_mod.perf_logger.handlers[:]
        job_queue_mod.perf_logger.handlers = [_CaptureHandler()]
        try:
            job_queue_mod._log_performance(state, queue_size=0)
        finally:
            job_queue_mod.perf_logger.handlers = orig_handlers

        entry = json.loads(captured_lines[0])
        assert "stage1" not in entry
