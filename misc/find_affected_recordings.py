#!/usr/bin/env python3
"""
find_affected_recordings.py — v19.16 N2 (Einmal-Skript, auf dem Pod ausführen)

Findet Aufnahmen, deren Transkript vermutlich unvollständig ist (Duration-
Schätzfehler bei Browser-webm, Fall aufnahme-79 vom 2026-08-04).

Heuristik: webm-Aufnahmen, deren gespeicherte duration_s verdächtig nahe an
der Dateigrößen-Schätzung (size/3000/1.05) liegt — d.h. die Dauer stammte aus
Stufe 4 und der letzte Abschnitt wurde beim Chunking abgeschnitten. Für noch
vorhandene Audiodateien (<24 h) wird die echte Dauer per ffmpeg nachgemessen.

Aufruf auf dem Pod:
    cd /workspace/scriptTelios/backend && python3 ../misc/find_affected_recordings.py
"""
import asyncio
import re
import subprocess
from pathlib import Path

RECORDINGS_DIR = Path("/workspace/recordings")


def real_duration(p: Path) -> float:
    r = subprocess.run(["ffmpeg", "-i", str(p), "-f", "null", "-"],
                       capture_output=True, text=True)
    times = re.findall(r"time=(\d+):(\d+):(\d+\.\d+)", r.stderr)
    if times:
        h, m, s = times[-1]
        return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


async def main():
    from app.core.database import async_session_factory
    from app.models.db import Recording
    from sqlalchemy import select

    async with async_session_factory() as db:
        res = await db.execute(
            select(Recording).where(
                Recording.status == "ready",
                Recording.deleted_at.is_(None),
            ).order_by(Recording.id)
        )
        recs = res.scalars().all()

    print(f"{len(recs)} fertige Aufnahmen. Prüfe auf Stufe-4-Signatur…\n")
    affected = []
    for r in recs:
        if not r.filename or not r.filename.endswith(".webm"):
            continue
        p = RECORDINGS_DIR / r.filename
        size = p.stat().st_size if p.exists() else None
        est = size / 3000 / 1.05 if size else None
        # Signatur: duration_s ≈ Dateigrößen-Schätzung (±2 %) → Stufe 4 war aktiv
        stage4 = est and r.duration_s and abs(r.duration_s - est) / est < 0.02
        real = real_duration(p) if p.exists() else 0.0
        gap = round(real - (r.duration_s or 0), 1) if real else None
        if stage4 or (gap and gap > 30):
            affected.append((r.id, r.label, r.duration_s, real or "Audio weg", gap))

    if not affected:
        print("Keine betroffenen Aufnahmen gefunden.")
        return
    print(f"{'ID':>5}  {'Label':<30} {'DB-Dauer':>9} {'Echt':>9} {'Lücke':>7}")
    for rid, label, dur, real, gap in affected:
        print(f"{rid:>5}  {(label or '—')[:30]:<30} {dur or 0:>8.0f}s "
              f"{real if isinstance(real, str) else f'{real:.0f}s':>9} "
              f"{'' if gap is None else f'{gap:.0f}s':>7}")
    print("\nEmpfehlung: Bei Aufnahmen mit vorhandenem Audio die Transkription "
          "in P0 erneut starten (nach v19.16-Deploy wird sie vollständig). "
          "Bei gelöschtem Audio (>24 h) ist die Lücke nicht mehr aufholbar — "
          "betroffene Dokumente kennzeichnen.")


if __name__ == "__main__":
    asyncio.run(main())
