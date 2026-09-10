"""v19.21 Sprint S2 (Lint-Gate): Tests fuer die beim Entblockieren des
Event-Loops extrahierten synchronen Helfer und den Fire-and-forget-Wrapper
in recordings.py (RUF006-Fix).
"""
import asyncio
import logging
import os
import time

import pytest


def test_delete_stale_uploads_loescht_nur_alte(tmp_path):
    from app.services.retention import _delete_stale_files as _delete_stale_uploads

    alt = tmp_path / "alt.pdf"
    neu = tmp_path / "neu.pdf"
    alt.write_text("a")
    neu.write_text("n")
    past = time.time() - 48 * 3600
    os.utime(str(alt), (past, past))

    assert _delete_stale_uploads(tmp_path, 24 * 3600) == 1
    assert not alt.exists()
    assert neu.exists()


def test_delete_stale_uploads_fehlendes_verzeichnis(tmp_path):
    from app.services.retention import _delete_stale_files as _delete_stale_uploads
    assert _delete_stale_uploads(tmp_path / "gibt_es_nicht", 10) == 0


def test_prune_log_file_entfernt_alte_zeilen(tmp_path):
    from app.services.retention import _prune_log_file

    p = tmp_path / "audit.log"
    now = time.time()
    p.write_text(
        '{"ts": %d, "m": "alt"}\n{"ts": %d, "m": "neu"}\nkein json\n'
        % (now - 100 * 86400, now),
        encoding="utf-8",
    )
    removed = _prune_log_file(p, max_age=30 * 86400)
    assert removed == 1
    rest = p.read_text(encoding="utf-8")
    assert '"neu"' in rest
    assert "kein json" in rest          # unparsebare Zeilen bleiben erhalten
    assert '"alt"' not in rest


def test_prune_log_file_ohne_datei(tmp_path):
    from app.services.retention import _prune_log_file
    assert _prune_log_file(tmp_path / "nope.log", 1) == 0


@pytest.mark.asyncio
async def test_spawn_background_haelt_referenz_und_loggt_fehler(caplog):
    from app.api import recordings as R

    async def _boom():
        raise RuntimeError("kaputt")

    async def _ok():
        return 1

    with caplog.at_level(logging.WARNING, logger="app.api.recordings"):
        R._spawn_background(_boom(), "boom")
        R._spawn_background(_ok(), "ok")
        assert len(R._background_tasks) == 2      # Referenz gehalten
        await asyncio.sleep(0.05)

    assert R._background_tasks == set()           # nach Abschluss wieder frei
    assert any("boom" in rec.getMessage() and "kaputt" in rec.getMessage()
               for rec in caplog.records)


# ── S4: Struktur-Fixes ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_job_from_db_nutzt_db_job_to_dict(monkeypatch):
    """get_job_from_db() und list_filtered() liefern dasselbe Schema - ein
    Konverter (_db_job_to_dict) statt zwei handgepflegter Kopien."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from app.services.job_queue import JobQueue

    q = JobQueue()
    db_job = MagicMock()
    sentinel = {"job_id": "x", "workflow": "dokumentation"}
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = db_job
    session.execute = AsyncMock(return_value=result)

    with patch("app.core.database.async_session_factory", return_value=session), \
         patch.object(JobQueue, "_db_job_to_dict", return_value=sentinel) as conv:
        out = await q.get_job_from_db("x")
    assert out is sentinel
    conv.assert_called_once_with(db_job)


@pytest.mark.asyncio
async def test_load_owned_recording_404_und_403():
    from unittest.mock import AsyncMock, MagicMock
    from fastapi import HTTPException
    from app.api.recordings import _load_owned_recording

    session = MagicMock()
    result = MagicMock()
    session.execute = AsyncMock(return_value=result)

    result.scalar_one_or_none.return_value = None
    with pytest.raises(HTTPException) as e:
        await _load_owned_recording(session, 1, "dr.a")
    assert e.value.status_code == 404

    rec = MagicMock(); rec.therapeut_id = "dr.b"
    result.scalar_one_or_none.return_value = rec
    with pytest.raises(HTTPException) as e:
        await _load_owned_recording(session, 1, "dr.a")
    assert e.value.status_code == 403

    rec.therapeut_id = None                      # Alt-Aufnahme ohne Owner
    assert await _load_owned_recording(session, 1, "dr.a") is rec
    rec.therapeut_id = "dr.a"
    assert await _load_owned_recording(session, 1, "dr.a") is rec


def test_match_heading_und_section_end():
    from app.services.extraction import _match_heading, _is_section_end
    hs = ["therapeutischer verlauf", "verlauf",
          "bisheriger behandlungsverlauf und aktueller stand"]
    # "verlauf" ist ein um ein Wort kuerzerer Treffer des langen Headings
    # (Substring-Regel >20 Zeichen, laengstes zuerst) - Alt-Verhalten beibehalten
    assert _match_heading("verlauf", hs) == "therapeutischer verlauf"
    assert _match_heading("verlauf ohne treffer", ["verlauf"]) == "verlauf"     # Praefix-Regel kurz
    assert _match_heading("therapeutischer verlauf", hs) == "therapeutischer verlauf"   # laengstes zuerst
    assert _match_heading("bisheriger behandlungsverlauf und aktueller stand der therapie", hs) \
        == "bisheriger behandlungsverlauf und aktueller stand"
    assert _match_heading("anamnese", hs) is None
    assert _is_section_end("wir bitten daher um verlängerung")
    assert not _is_section_end("die patientin berichtet")
