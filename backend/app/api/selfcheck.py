"""
GET /api/selfcheck – Subsystem-Selbstprüfung des Pods.

Prüft Ollama, Pflicht-Modelle, PostgreSQL, freien Plattenplatz und GPU und
aggregiert zu einem Gesamtstatus (ok | degraded | down). Reine Introspektion:
kein Scheduling, keine Benachrichtigung – das übernimmt der Cloudflare-Worker
(Cron + Telegram + KV), damit Secrets/Logik nicht dupliziert werden.

Ergebnis wird kurz gecacht (SELFCHECK_TTL), damit Polling den Pod nicht belastet.
"""
import asyncio
import logging
import shutil
import subprocess
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine
from app.services.embeddings import EMBEDDING_MODEL

router = APIRouter()
logger = logging.getLogger(__name__)

DISK_PATH = "/workspace"
DISK_MIN_GB = 10.0
SELFCHECK_TTL = 20.0  # Sekunden

_CACHE: dict = {"ts": 0.0, "result": None}


# ── Modell-Abgleich ────────────────────────────────────────────────

def _model_present(required: str, installed: set[str]) -> bool:
    """Ist ein Pflichtmodell installiert? Mit Tag → exakter Match; ohne Tag →
    beliebiger Tag derselben Basis (z.B. 'mistral-small3.2' ~ '…:latest')."""
    req = (required or "").strip()
    if not req:
        return True
    if req in installed:
        return True
    if ":" in req:
        return False  # Tag angegeben → exakte Übereinstimmung verlangt
    return any(name.split(":")[0] == req for name in installed)


def _required_models() -> set[str]:
    req: set[str] = set()
    if settings.OLLAMA_MODEL:
        req.add(settings.OLLAMA_MODEL)
    if getattr(settings, "SUMMARY_MODEL", None):
        req.add(settings.SUMMARY_MODEL)
    for v in getattr(settings, "WORKFLOW_MODEL", {}).values():
        if v:
            req.add(v)
    if EMBEDDING_MODEL:
        req.add(EMBEDDING_MODEL)
    return req


# ── Einzelne Probes ────────────────────────────────────────────────

async def _tags() -> tuple[bool, set[str]]:
    """Ollama /api/tags → (erreichbar, Menge installierter Modellnamen)."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"{settings.OLLAMA_HOST}/api/tags")
        if r.status_code != 200:
            return False, set()
        data = r.json()
        return True, {m.get("name", "") for m in data.get("models", [])}
    except Exception:
        return False, set()


async def _check_db() -> dict:
    try:
        async def _q():
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        await asyncio.wait_for(_q(), timeout=3.0)
        return {"ok": True, "detail": "SELECT 1 ok"}
    except Exception as e:
        return {"ok": False, "detail": type(e).__name__}


def _check_disk() -> dict:
    try:
        u = shutil.disk_usage(DISK_PATH)
        free_gb = round(u.free / (1024 ** 3), 1)
        return {"ok": free_gb >= DISK_MIN_GB, "free_gb": free_gb, "detail": f"{free_gb} GB frei"}
    except Exception as e:
        return {"ok": False, "detail": f"{DISK_PATH}: {type(e).__name__}"}


def _check_gpu() -> dict:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0:
            return {"ok": False, "detail": f"nvidia-smi rc={out.returncode}"}
        line = (out.stdout or "").strip().splitlines()
        if not line:
            return {"ok": False, "detail": "keine GPU gelistet"}
        parts = [p.strip() for p in line[0].split(",")]
        name, total, free = parts[0], parts[1], parts[2]
        return {"ok": True, "detail": f"{name} – frei {free}/{total} MB"}
    except FileNotFoundError:
        return {"ok": False, "detail": "nvidia-smi nicht gefunden"}
    except Exception as e:
        return {"ok": False, "detail": type(e).__name__}


# ── Aggregation ────────────────────────────────────────────────────

def _aggregate(checks: dict) -> str:
    if not (checks["ollama"]["ok"] and checks["db"]["ok"] and checks["gpu"]["ok"]):
        return "down"
    if not (checks["models"]["ok"] and checks["disk"]["ok"]):
        return "degraded"
    return "ok"


async def _run_selfcheck() -> dict:
    reachable, installed = await _tags()
    ollama = {"ok": reachable, "detail": "OK" if reachable else "unerreichbar"}

    if reachable:
        missing = sorted(m for m in _required_models() if not _model_present(m, installed))
        models = {"ok": len(missing) == 0, "missing": missing,
                  "detail": "alle vorhanden" if not missing else "fehlt: " + ", ".join(missing)}
    else:
        models = {"ok": False, "missing": [], "detail": "Ollama nicht erreichbar"}

    db = await _check_db()
    disk = await asyncio.to_thread(_check_disk)
    gpu = await asyncio.to_thread(_check_gpu)
    whisper = {"ok": True, "detail": f"{settings.WHISPER_MODEL} / {settings.WHISPER_DEVICE}"}

    checks = {"ollama": ollama, "models": models, "db": db, "disk": disk, "gpu": gpu, "whisper": whisper}
    return {"status": _aggregate(checks), "ts": datetime.now(timezone.utc).isoformat(), "checks": checks}


@router.get("/selfcheck")
async def selfcheck():
    now = time.monotonic()
    if _CACHE["result"] is None or now - _CACHE["ts"] > SELFCHECK_TTL:
        try:
            _CACHE["result"] = await _run_selfcheck()
        except Exception as e:
            logger.exception("selfcheck failed")
            _CACHE["result"] = {"status": "down", "ts": datetime.now(timezone.utc).isoformat(),
                                "checks": {}, "error": type(e).__name__}
        _CACHE["ts"] = now
    return _CACHE["result"]
