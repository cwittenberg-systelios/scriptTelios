"""tests/unit/test_v1920_sprint_d.py — v19.20 Sprint D: Aufnahmedauer beim Upload."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch
import pytest


class _Rec:
    def __init__(self, dur=None):
        self.id = 7; self.duration_s = dur


class _Result:
    def __init__(self, rec): self._rec = rec
    def scalar_one_or_none(self): return self._rec


class _Session:
    def __init__(self, rec): self.rec = rec; self.committed = False
    async def execute(self, *a, **k): return _Result(self.rec)
    async def commit(self): self.committed = True


def _factory(session):
    @asynccontextmanager
    async def _f():
        yield session
    return _f


class TestSetDurationEarly:
    @pytest.mark.asyncio
    async def test_setzt_dauer_wenn_leer(self):
        from app.api import recordings as R
        rec = _Rec(None); sess = _Session(rec)
        with patch.object(R, "async_session_factory", _factory(sess)), \
             patch("app.services.transcription._get_duration", lambda p: 3758.7):
            await R._set_duration_early(7, Path("/tmp/x.webm"))
        assert rec.duration_s == 3758.7 and sess.committed

    @pytest.mark.asyncio
    async def test_ueberschreibt_nicht(self):
        from app.api import recordings as R
        rec = _Rec(120.0); sess = _Session(rec)
        with patch.object(R, "async_session_factory", _factory(sess)), \
             patch("app.services.transcription._get_duration", lambda p: 999.0):
            await R._set_duration_early(7, Path("/tmp/x.webm"))
        assert rec.duration_s == 120.0 and not sess.committed

    @pytest.mark.asyncio
    async def test_fehler_bleibt_stumm(self):
        from app.api import recordings as R
        def boom(p): raise RuntimeError("ffprobe fehlt")
        with patch("app.services.transcription._get_duration", boom):
            await R._set_duration_early(7, Path("/tmp/x.webm"))  # darf nicht werfen

    def test_upload_startet_vorab_dauer(self):
        import inspect
        from app.api import recordings as R
        src = inspect.getsource(R.upload_recording)
        assert "_set_duration_early" in src
