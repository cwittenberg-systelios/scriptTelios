"""Unit-Tests für /api/selfcheck – Modell-Abgleich + Statusaggregation.

Die realen Probes (Ollama/DB/Disk/GPU) werden gemockt; getestet wird die Logik.
Läuft auf dem Pod (echte App-Imports)."""
import asyncio
import time

import pytest

from app.api import selfcheck


# ── Modell-Matching ────────────────────────────────────────────────

INSTALLED = {"mistral-small3.2:latest", "gemma4:31b", "nomic-embed-text:latest"}


def test_model_present_no_tag_matches_base():
    assert selfcheck._model_present("mistral-small3.2", INSTALLED)
    assert selfcheck._model_present("nomic-embed-text", INSTALLED)


def test_model_present_exact_tag():
    assert selfcheck._model_present("gemma4:31b", INSTALLED)


def test_model_present_wrong_tag_fails():
    assert not selfcheck._model_present("gemma4:9b", INSTALLED)


def test_model_present_missing_fails():
    assert not selfcheck._model_present("llava", INSTALLED)


def test_model_present_empty_is_ok():
    assert selfcheck._model_present("", INSTALLED)


# ── Aggregation ────────────────────────────────────────────────────

def _checks(ollama=True, models=True, db=True, disk=True, gpu=True):
    return {
        "ollama": {"ok": ollama}, "models": {"ok": models}, "db": {"ok": db},
        "disk": {"ok": disk}, "gpu": {"ok": gpu}, "whisper": {"ok": True},
    }


def test_aggregate_all_ok():
    assert selfcheck._aggregate(_checks()) == "ok"


def test_aggregate_missing_model_degraded():
    assert selfcheck._aggregate(_checks(models=False)) == "degraded"


def test_aggregate_low_disk_degraded():
    assert selfcheck._aggregate(_checks(disk=False)) == "degraded"


def test_aggregate_db_down():
    assert selfcheck._aggregate(_checks(db=False)) == "down"


def test_aggregate_gpu_down():
    assert selfcheck._aggregate(_checks(gpu=False)) == "down"


def test_aggregate_ollama_down():
    assert selfcheck._aggregate(_checks(ollama=False)) == "down"


# ── _run_selfcheck mit gemockten Probes ────────────────────────────

def _patch(monkeypatch, *, tags_ok=True, installed=None, db_ok=True, disk_ok=True, gpu_ok=True):
    installed = installed if installed is not None else INSTALLED

    async def fake_tags():
        return tags_ok, (installed if tags_ok else set())

    async def fake_db():
        return {"ok": db_ok, "detail": "SELECT 1 ok" if db_ok else "OperationalError"}

    monkeypatch.setattr(selfcheck, "_tags", fake_tags)
    monkeypatch.setattr(selfcheck, "_check_db", fake_db)
    monkeypatch.setattr(selfcheck, "_check_gpu", lambda: {"ok": gpu_ok, "detail": "RTX – frei 30000/32000 MB" if gpu_ok else "nvidia-smi nicht gefunden"})
    # Disk läuft im Hintergrund → Cache direkt vorbelegen (frisch, damit kein Task startet).
    monkeypatch.setattr(selfcheck, "_DISK_CACHE", {
        "ts": time.monotonic(),
        "result": {"ok": disk_ok, "free_gb": 50.0, "detail": "50 GB frei"},
        "running": False, "task": None,
    })


def test_run_all_ok(monkeypatch):
    _patch(monkeypatch)
    res = asyncio.run(selfcheck._run_selfcheck())
    assert res["status"] == "ok"
    assert res["checks"]["models"]["ok"]
    assert res["checks"]["models"]["missing"] == []


def test_run_missing_model_degraded(monkeypatch):
    # gemma4:31b fehlt in der Installation → degraded + missing gelistet
    _patch(monkeypatch, installed={"mistral-small3.2:latest", "nomic-embed-text:latest"})
    res = asyncio.run(selfcheck._run_selfcheck())
    assert res["status"] == "degraded"
    assert "gemma4:31b" in res["checks"]["models"]["missing"]


def test_run_db_down(monkeypatch):
    _patch(monkeypatch, db_ok=False)
    res = asyncio.run(selfcheck._run_selfcheck())
    assert res["status"] == "down"


def test_run_gpu_absent_down(monkeypatch):
    _patch(monkeypatch, gpu_ok=False)
    res = asyncio.run(selfcheck._run_selfcheck())
    assert res["status"] == "down"


def test_run_ollama_unreachable_down(monkeypatch):
    _patch(monkeypatch, tags_ok=False)
    res = asyncio.run(selfcheck._run_selfcheck())
    assert res["status"] == "down"
    assert not res["checks"]["models"]["ok"]


# ── _check_disk (Backing-Store-Erkennung + Quota/du) ──

import collections as _collections

def _fake_du(total_gb, free_gb):
    D = _collections.namedtuple("D", "total used free")
    return D(int(total_gb * 1024 ** 3), 0, int(free_gb * 1024 ** 3))


def test_disk_backing_store_is_ok_with_hint(monkeypatch):
    monkeypatch.setattr(selfcheck, "DISK_QUOTA_GB", 0.0)
    monkeypatch.setattr(selfcheck.shutil, "disk_usage", lambda p: _fake_du(748434.7, 748434.7))
    r = selfcheck._check_disk()
    assert r["ok"] is True and "Backing-Store" in r["detail"]


def test_disk_normal_low_free_degraded(monkeypatch):
    monkeypatch.setattr(selfcheck, "DISK_QUOTA_GB", 0.0)
    monkeypatch.setattr(selfcheck, "DISK_MIN_GB", 10.0)
    monkeypatch.setattr(selfcheck.shutil, "disk_usage", lambda p: _fake_du(100, 5))
    r = selfcheck._check_disk()
    assert r["ok"] is False and r["free_gb"] == 5.0


def test_disk_quota_uses_du(monkeypatch):
    monkeypatch.setattr(selfcheck, "DISK_QUOTA_GB", 70.0)
    monkeypatch.setattr(selfcheck, "DISK_MIN_GB", 10.0)
    monkeypatch.setattr(selfcheck, "_du_gb", lambda p: 60.0)
    r = selfcheck._check_disk()
    assert r["ok"] is True and r["free_gb"] == 10.0 and "von 70 GB" in r["detail"]


def test_disk_quota_low_free_degraded(monkeypatch):
    monkeypatch.setattr(selfcheck, "DISK_QUOTA_GB", 70.0)
    monkeypatch.setattr(selfcheck, "DISK_MIN_GB", 10.0)
    monkeypatch.setattr(selfcheck, "_du_gb", lambda p: 65.0)
    r = selfcheck._check_disk()
    assert r["ok"] is False and r["free_gb"] == 5.0


# ── Nicht-blockierende Disk-Messung + Uptime ──

def test_disk_cached_does_not_block_and_uses_placeholder(monkeypatch):
    """Erster Aufruf darf nicht auf `du` warten (sonst laeuft der Worker in den Timeout)."""
    monkeypatch.setattr(selfcheck, "_DISK_CACHE", {"ts": 0.0, "result": None, "running": False, "task": None})

    def slow_disk():
        time.sleep(2.0)
        return {"ok": True, "detail": "12 GB frei von 70 GB"}

    monkeypatch.setattr(selfcheck, "_check_disk", slow_disk)

    async def go():
        t0 = time.monotonic()
        first = await selfcheck._disk_cached()
        elapsed = time.monotonic() - t0
        await asyncio.sleep(2.4)                       # Hintergrundlauf abwarten
        second = await selfcheck._disk_cached()
        return elapsed, first, second

    elapsed, first, second = asyncio.run(go())
    assert elapsed < 0.5                                # blockiert nicht
    assert "wird ermittelt" in first["detail"]
    assert "70 GB" in second["detail"]                  # Hintergrundwert liegt vor


def test_run_includes_uptime(monkeypatch):
    _patch(monkeypatch)
    res = asyncio.run(selfcheck._run_selfcheck())
    assert isinstance(res["uptime_sec"], int) and res["uptime_sec"] >= 0
