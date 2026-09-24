"""
interview_lease.py - Vorrang fuer laufende Interviews (v19.34).

Solange nur EINE GPU da ist (GPU_PROFILE=single), teilen sich Dialog und
Jobs gemma bzw. wechseln das Modell. Ein laufendes Interview haelt deshalb
eine befristete Reservierung ("Lease"): neue LLM-Jobs und P0-Transkriptionen
starten nicht, solange eine Reservierung aktiv ist.

Regeln (Entscheidungen 24.09.):
  - Aktivitaet verlaengert: Dialog-/Karten-Turn, Aufnahme-Start, Diktat.
    Ein nur offener Tab verlaengert NICHT.
  - Ablauf nach INTERVIEW_LEASE_IDLE_S (5 min) ohne Aktivitaet (Pause,
    vergessener Tab).
  - Freigabe sofort bei fertig/verworfen/Tab geschlossen.
  - Kein Job wartet laenger als INTERVIEW_MAX_JOB_WAIT_S (10 min), danach
    startet er trotzdem.
  - Ein bereits laufender Job wird nicht unterbrochen.
  - GPU_PROFILE=dual oder INTERVIEW_PRIORITY=false: keine Wirkung.

Zustand liegt im Prozess (ein uvicorn-Worker auf dem Pod) - nach einem
Neustart gibt es keine Reservierungen mehr, was gewollt ist.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Lease:
    session_id: str
    user: str
    last_activity: float
    created: float


_leases: dict[str, Lease] = {}


def enabled() -> bool:
    return bool(getattr(settings, "INTERVIEW_PRIORITY", True)) and not settings.gpu_dual


def idle_s() -> int:
    return int(getattr(settings, "INTERVIEW_LEASE_IDLE_S", 300))


def max_wait_s() -> int:
    return int(getattr(settings, "INTERVIEW_MAX_JOB_WAIT_S", 600))


def _purge(now: Optional[float] = None) -> None:
    now = time.monotonic() if now is None else now
    for sid in [s for s, le in _leases.items() if now - le.last_activity > idle_s()]:
        logger.info("Interview-Reservierung %s abgelaufen (keine Aktivitaet seit %d s)", sid, idle_s())
        _leases.pop(sid, None)


def touch(session_id: Optional[str], user: str = "") -> bool:
    """Legt eine Reservierung an oder verlaengert sie. False ohne Session-ID
    oder wenn der Vorrang aus ist."""
    if not session_id or not enabled():
        return False
    now = time.monotonic()
    le = _leases.get(session_id)
    if le is None:
        _leases[session_id] = Lease(session_id, user, now, now)
        logger.info("Interview-Reservierung %s (user=%s) - neue Jobs warten", session_id, user)
    else:
        le.last_activity = now
    return True


def release(session_id: Optional[str]) -> bool:
    if session_id and _leases.pop(session_id, None) is not None:
        logger.info("Interview-Reservierung %s freigegeben", session_id)
        return True
    return False


def active_count() -> int:
    if not enabled():
        return 0
    _purge()
    return len(_leases)


def is_active() -> bool:
    return active_count() > 0


def reset() -> None:
    """Fuer Tests."""
    _leases.clear()


async def wait_until_free(
    *,
    is_cancelled: Callable[[], bool] = lambda: False,
    on_wait: Optional[Callable[[int], None]] = None,
    poll_s: float = 5.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> float:
    """Wartet, bis keine Reservierung aktiv ist - hoechstens max_wait_s().
    on_wait(rest_min) wird bei jedem Durchgang aufgerufen (Fortschrittsanzeige).
    Liefert die gewartete Zeit in Sekunden."""
    if not is_active():
        return 0.0
    t0 = clock()
    while is_active() and not is_cancelled():
        waited = clock() - t0
        if waited >= max_wait_s():
            logger.info("Job wartete %d s auf ein Interview - startet jetzt trotzdem", int(waited))
            break
        if on_wait is not None:
            on_wait(max(1, int((max_wait_s() - waited + 59) // 60)))
        await sleep(poll_s)
    return round(clock() - t0, 1)
