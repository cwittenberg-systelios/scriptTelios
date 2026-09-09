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
    from app.main import _delete_stale_uploads

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
    from app.main import _delete_stale_uploads
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
