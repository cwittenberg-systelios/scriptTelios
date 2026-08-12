"""
tests/unit/test_retention_db.py
───────────────────────────────
§6a-Retention (Einwilligung v1.1, Sprint 2026-08-12): Jobs und Recordings
mit Patienteninhalten werden nach RETENTION['qa_artifacts'] (90 Tage) hart
aus der DB gelöscht.

Selbst-versorgt (eigene Tabellen-Fixture) statt integration/conftest, da
tests/conftest.py (Import-Abhängigkeit der Integration-Conftest) nicht im
Repo liegt. Voraussetzung wie überall: DATABASE_URL zeigt beim Testlauf
auf SQLite (sqlite+aiosqlite:///...).
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.database import async_session_factory
from app.models.db import Job, Recording
from app.services.retention import (
    RETENTION,
    cleanup_jobs_db,
    cleanup_recordings_db,
)


@pytest.fixture(autouse=True)
def _db_tables():
    """Tabellen pro Test anlegen/abbauen (SQLite, eigener Loop)."""
    from app.core.database import engine, Base

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def teardown():
        await asyncio.sleep(0.05)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(setup())
        yield
        loop.run_until_complete(teardown())
    finally:
        loop.close()



def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _age(days: int) -> datetime:
    return datetime.now(tz=timezone.utc) - timedelta(days=days)


OLD_DAYS = RETENTION["qa_artifacts"] // 86400 + 5      # deutlich jenseits der Frist
YOUNG_DAYS = RETENTION["qa_artifacts"] // 86400 - 5    # deutlich innerhalb


class TestJobsRetention:
    def test_old_jobs_deleted_young_kept(self):
        async def scenario():
            async with async_session_factory() as db:
                db.add(Job(id="old-job-1", workflow="p1", status="done",
                           created_at=_age(OLD_DAYS),
                           result_transcript="T: sensibles Transkript"))
                db.add(Job(id="young-job-1", workflow="p1", status="done",
                           created_at=_age(YOUNG_DAYS),
                           result_transcript="T: aktuelles Transkript"))
                await db.commit()

            deleted = await cleanup_jobs_db()

            async with async_session_factory() as db:
                remaining = [j.id for j in (await db.execute(
                    __import__("sqlalchemy").select(Job))).scalars()]
            return deleted, remaining

        deleted, remaining = _run(scenario())
        assert deleted == 1
        assert remaining == ["young-job-1"]

    def test_unfinished_old_job_deleted_by_created_at(self):
        """finished_at=NULL (abgebrochen) darf die Löschung nicht verhindern."""
        async def scenario():
            async with async_session_factory() as db:
                db.add(Job(id="old-stuck", workflow="p3", status="error",
                           created_at=_age(OLD_DAYS), finished_at=None))
                await db.commit()
            return await cleanup_jobs_db()

        assert _run(scenario()) == 1

    def test_noop_on_empty_db(self):
        assert _run(cleanup_jobs_db()) == 0


class TestRecordingsRetention:
    def test_old_recordings_deleted_including_softdeleted(self, tmp_path, monkeypatch):
        from app.core import files as files_mod
        monkeypatch.setattr(files_mod, "recordings_dir", lambda: tmp_path)

        # Audiodatei-Rest für die alte Aufnahme anlegen — muss mit weg.
        (tmp_path / "old.webm").write_bytes(b"x")

        async def scenario():
            async with async_session_factory() as db:
                db.add(Recording(filename="old.webm", status="ready",
                                 created_at=_age(OLD_DAYS),
                                 transcript="altes Transkript"))
                db.add(Recording(filename="gone.webm", status="ready",
                                 created_at=_age(OLD_DAYS),
                                 deleted_at=_age(OLD_DAYS - 1),
                                 transcript="soft-deleted Transkript"))
                db.add(Recording(filename="young.webm", status="ready",
                                 created_at=_age(YOUNG_DAYS),
                                 transcript="junges Transkript"))
                await db.commit()

            deleted = await cleanup_recordings_db()

            async with async_session_factory() as db:
                remaining = [r.filename for r in (await db.execute(
                    __import__("sqlalchemy").select(Recording))).scalars()]
            return deleted, remaining

        deleted, remaining = _run(scenario())
        assert deleted == 2
        assert remaining == ["young.webm"]
        assert not (tmp_path / "old.webm").exists()

    def test_missing_file_does_not_block_db_delete(self, tmp_path, monkeypatch):
        from app.core import files as files_mod
        monkeypatch.setattr(files_mod, "recordings_dir", lambda: tmp_path)

        async def scenario():
            async with async_session_factory() as db:
                db.add(Recording(filename="never-existed.webm", status="ready",
                                 created_at=_age(OLD_DAYS)))
                await db.commit()
            return await cleanup_recordings_db()

        assert _run(scenario()) == 1


class TestFeedbackLogRetention:
    def test_backup_days_is_90(self):
        """§6a: feedback.log darf nicht länger als 90 Tage rotiert aufbewahren."""
        from app.api.feedback import _FEEDBACK_LOG_BACKUP_DAYS
        assert _FEEDBACK_LOG_BACKUP_DAYS == 90
