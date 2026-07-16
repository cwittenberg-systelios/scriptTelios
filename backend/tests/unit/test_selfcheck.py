"""Unit-Tests für /api/selfcheck – Modell-Abgleich + Statusaggregation.

Die realen Probes (Ollama/DB/Disk/GPU) werden gemockt; getestet wird die Logik.
Läuft auf dem Pod (echte App-Imports)."""
import asyncio

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
    monkeypatch.setattr(selfcheck, "_check_disk", lambda: {"ok": disk_ok, "free_gb": 50.0, "detail": "50 GB frei"})
    monkeypatch.setattr(selfcheck, "_check_gpu", lambda: {"ok": gpu_ok, "detail": "RTX – frei 30000/32000 MB" if gpu_ok else "nvidia-smi nicht gefunden"})


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
