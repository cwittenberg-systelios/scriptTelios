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
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from app.core.config import settings
from app.core.database import engine
from app.middleware.activity import idle_seconds
from app.services.embeddings import EMBEDDING_MODEL

router = APIRouter()
logger = logging.getLogger(__name__)

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default

# Disk-Prüfung konfigurierbar:
#   SELFCHECK_DISK_PATH        Pfad, dessen Belegung geprüft wird (Default /workspace)
#   SELFCHECK_DISK_QUOTA_GB    Volume-Quota in GB. Bei Netzwerk-Volumes meldet statvfs den
#                              (riesigen) Backing-Store statt der Quota — mit dieser Angabe
#                              wird stattdessen die echte Belegung (du) gegen die Quota geprüft.
#   SELFCHECK_DISK_MIN_FREE_GB Warnschwelle freier Platz (Default 10)
DISK_PATH = os.environ.get("SELFCHECK_DISK_PATH", "/workspace")
DISK_QUOTA_GB = _env_float("SELFCHECK_DISK_QUOTA_GB", 0.0)      # 0 = nicht gesetzt
DISK_MIN_GB = _env_float("SELFCHECK_DISK_MIN_FREE_GB", 10.0)
DISK_BACKING_STORE_TB = 5.0                                    # >5 TB total ⇒ Backing-Store, Quota nötig
# `du` über ein volles Volume dauert Sekunden bis Minuten → NIE im Antwortpfad.
# Es läuft im Hintergrund und wird lange gecacht; die Antwort nutzt den letzten Wert.
DISK_CACHE_TTL = _env_float("SELFCHECK_DISK_TTL", 600.0)        # 10 min
SELFCHECK_TTL = 10.0  # Sekunden (Probes sind billig — Disk laeuft im Hintergrund)

_PROCESS_START = time.time()   # Modul-Import ≈ uvicorn-Start → Uptime für Startup-Erkennung

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


async def _check_activity() -> dict:
    """Belegtheit des Pods fuer den Idle-Auto-Stopp im Cloudflare-Worker.

    Bewusst KEIN Gesundheitszustand: das Ergebnis steht als Top-Level-Key
    `activity` neben `checks` und geht nicht in _aggregate() ein — ein idler
    Pod ist nicht "degraded".

    `active_jobs` zaehlt laufende Generierungen UND laufende Transkriptionen.
    Beide Status-Spalten sind indiziert, die Query laeuft im 10-s-Cache mit.

    Bei Fehler `ok: False` — der Worker verzichtet dann auf den Idle-Stopp
    (Degradations-Fallback); die harte Nachtabschaltung greift weiterhin.
    """
    try:
        async def _q():
            async with engine.connect() as conn:
                res = await conn.execute(text(
                    "SELECT (SELECT count(*) FROM jobs "
                    "          WHERE status IN ('pending','running')) "
                    "     + (SELECT count(*) FROM recordings "
                    "          WHERE status IN ('uploading','transcribing') "
                    "            AND deleted_at IS NULL) AS active"
                ))
                return int(res.scalar() or 0)
        active = await asyncio.wait_for(_q(), timeout=3.0)
        return {"ok": True, "active_jobs": active, "idle_sec": round(idle_seconds())}
    except Exception as e:
        return {"ok": False, "detail": type(e).__name__}


def _du_gb(path: str) -> float | None:
    """Belegter Platz eines Verzeichnisses in GB via `du -sb` (auch bei Permission-Fehlern
    wird die Teilsumme aus stdout gelesen). Laeuft nur im Hintergrund → grosszuegiger Timeout."""
    try:
        out = subprocess.run(["du", "-sb", path], capture_output=True, text=True, timeout=180)
        parts = (out.stdout or "").split()
        if parts and parts[0].isdigit():
            return int(parts[0]) / (1024 ** 3)
        return None
    except Exception:
        return None


_DISK_CACHE: dict = {"ts": 0.0, "result": None, "running": False, "task": None}


async def _refresh_disk() -> None:
    """Disk-Messung im Hintergrund; blockiert nie eine Antwort."""
    try:
        _DISK_CACHE["result"] = await asyncio.to_thread(_check_disk)
    except Exception as e:
        _DISK_CACHE["result"] = {"ok": True, "detail": f"nicht messbar ({type(e).__name__})"}
    finally:
        _DISK_CACHE["ts"] = time.monotonic()
        _DISK_CACHE["running"] = False


async def _disk_cached() -> dict:
    """Letzten bekannten Disk-Wert liefern und bei Bedarf eine Hintergrund-Aktualisierung
    anstossen. Beim allerersten Aufruf liegt noch kein Wert vor → neutraler Platzhalter."""
    now = time.monotonic()
    stale = _DISK_CACHE["result"] is None or (now - _DISK_CACHE["ts"]) > DISK_CACHE_TTL
    if stale and not _DISK_CACHE["running"]:
        _DISK_CACHE["running"] = True
        try:
            _DISK_CACHE["task"] = asyncio.create_task(_refresh_disk())   # Referenz halten (GC)
        except RuntimeError:
            _DISK_CACHE["running"] = False
    if _DISK_CACHE["result"] is None:
        return {"ok": True, "detail": "wird ermittelt\u2026"}
    return _DISK_CACHE["result"]


def _check_disk() -> dict:
    try:
        # Mit Quota: echte Belegung gegen die Quota prüfen (korrekt für Netzwerk-Volumes).
        if DISK_QUOTA_GB > 0:
            used_gb = _du_gb(DISK_PATH)
            if used_gb is None:
                return {"ok": True, "detail": f"{DISK_PATH}: Belegung nicht messbar"}
            free_gb = round(DISK_QUOTA_GB - used_gb, 1)
            return {"ok": free_gb >= DISK_MIN_GB, "free_gb": free_gb, "used_gb": round(used_gb, 1),
                    "detail": f"{free_gb} GB frei von {DISK_QUOTA_GB:.0f} GB (belegt {used_gb:.1f} GB)"}

        # Ohne Quota: statvfs — aber Backing-Store eines Netzwerk-Volumes erkennen.
        u = shutil.disk_usage(DISK_PATH)
        total_gb = u.total / (1024 ** 3)
        free_gb = round(u.free / (1024 ** 3), 1)
        if total_gb > DISK_BACKING_STORE_TB * 1024:
            return {"ok": True, "free_gb": free_gb,
                    "detail": f"{free_gb} GB frei (Backing-Store — SELFCHECK_DISK_QUOTA_GB setzen für echte Volume-Belegung)"}
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
    # Alle Probes parallel → Gesamtdauer = langsamste Probe (statt Summe).
    (reachable, installed), db, disk, gpu, activity = await asyncio.gather(
        _tags(),
        _check_db(),
        _disk_cached(),                    # blockiert nie (Hintergrund-Cache)
        asyncio.to_thread(_check_gpu),
        _check_activity(),
    )
    ollama = {"ok": reachable, "detail": "OK" if reachable else "unerreichbar"}

    if reachable:
        missing = sorted(m for m in _required_models() if not _model_present(m, installed))
        models = {"ok": len(missing) == 0, "missing": missing,
                  "detail": "alle vorhanden" if not missing else "fehlt: " + ", ".join(missing)}
    else:
        models = {"ok": False, "missing": [], "detail": "Ollama nicht erreichbar"}

    whisper = {"ok": True, "detail": f"{settings.WHISPER_MODEL} / {settings.WHISPER_DEVICE}"}

    checks = {"ollama": ollama, "models": models, "db": db, "disk": disk, "gpu": gpu, "whisper": whisper}
    return {
        "status": _aggregate(checks),
        "ts": datetime.now(timezone.utc).isoformat(),
        "uptime_sec": round(time.time() - _PROCESS_START),   # fuer die Startup-Erkennung
        "checks": checks,
        "activity": activity,   # Idle-Auto-Stopp (Worker) — kein Gesundheitszustand
    }


@router.get("/selfcheck")
async def selfcheck():
    now = time.monotonic()
    if _CACHE["result"] is None or now - _CACHE["ts"] > SELFCHECK_TTL:
        try:
            # Hartes Zeitbudget: die Antwort darf nie am haengenden Probe kleben
            # (der Aufrufer laeuft sonst in seinen eigenen Timeout).
            _CACHE["result"] = await asyncio.wait_for(_run_selfcheck(), timeout=8.0)
        except Exception as e:
            logger.exception("selfcheck failed")
            _CACHE["result"] = {"status": "down", "ts": datetime.now(timezone.utc).isoformat(),
                                "uptime_sec": round(time.time() - _PROCESS_START),
                                "checks": {}, "error": type(e).__name__}
        _CACHE["ts"] = now
    return _CACHE["result"]
