# Patch-Plan: Job-Retention + Repair-Cascade

**Status:** offen (nicht in Phase C umgesetzt)
**Priorität:** mittel — kein akutes Datenschutz-Risiko solange `RETENTION["jobs_*"]` nicht scharfgeschaltet ist
**Aufwand:** ~2h Implementation + Tests
**Verwandt:** `docs/dsgvo/repair_audit.md`

---

## Ausgangslage

`app/services/retention.py` definiert zwei Konstanten:
```python
"jobs_done":   1 * 3600,   # 1h nach Abschluss
"jobs_error": 24 * 3600,   # 24h zur Fehleranalyse
```
Aber: **keine `cleanup_jobs_*()`-Funktion existiert**, und `retention_task()`
ruft auch keine auf. Die Konstanten sind tot. Job-Rows werden faktisch nie
aus der DB entfernt (`DELETE /jobs/{id}` ist Cancel, nicht Delete).

Das ist nicht durch Phase C entstanden, schon vor v19 so. Phase C macht das
Problem aber zugespitzter, weil Repair-Jobs in `repair_input_json.final_prompt`
**eine zweite Kopie des Original-Klartextes** halten. Ohne Cascade-Awareness
können Repair-Kinder den Parent überleben und das Datenschutz-Konzept
unterlaufen.

---

## Drei Trigger, drei Cascade-Szenarien

| # | Trigger | Was muss kaskadieren | Aktuell vorhanden? |
|---|---------|----------------------|---------------------|
| 1 | Auto-Retention `jobs_done` (Zeit-basiert) | Parent + alle Repair-Kinder gleichzeitig | nein |
| 2 | Therapeut-initiierter Delete | Parent + alle Repair-Kinder | Endpoint existiert nicht |
| 3 | Anonymisierung (z.B. Namens-Maskierung) | `result_text`, `befund_text`, `repair_input_json.final_prompt` | nicht implementiert |

Phase C berührt nur das Datenmodell — alle drei bleiben offen.

---

## Patch 1 — Cascade-Helper (Pflichtteil, ohne den Rest nichts machen)

**Ziel:** Eine Funktion `delete_job_with_descendants(job_id)` die einen Job
samt aller Repair-Kinder atomar löscht. Wird von Patch 2 und Patch 3
verwendet, kann aber eigenständig vom UI/CLI getriggert werden.

**Datei:** `app/services/retention.py`

```python
async def delete_job_with_descendants(job_id: str) -> dict[str, int]:
    """Loescht einen Job und alle Repair-Kinder atomar.

    Phase C garantiert: parent_job_id zeigt auf den DIREKTEN Vorgaenger.
    Eine Repair-Kette ist daher ein Baum, kein DAG. Wir traversieren BFS
    bis keine Kinder mehr da sind. In der Praxis ist die Kette flach
    (1-2 Repair-Stufen), aber der Code laesst beliebige Tiefe zu.

    Returns: {"job": 1, "repairs": N, "total": 1+N}
    """
    from app.core.database import async_session_factory
    from app.models.db import Job
    from sqlalchemy import delete, select

    async with async_session_factory() as db:
        # 1. Alle Descendants per BFS sammeln (parent_job_id ist indiziert)
        to_visit = [job_id]
        descendants: list[str] = []
        while to_visit:
            current = to_visit.pop()
            result = await db.execute(
                select(Job.id).where(Job.parent_job_id == current)
            )
            children = [row[0] for row in result]
            descendants.extend(children)
            to_visit.extend(children)

        # 2. Ein einziger DELETE: Parent + Descendants (kein FK-Cascade,
        # daher manuell).
        all_ids = [job_id] + descendants
        result = await db.execute(
            delete(Job).where(Job.id.in_(all_ids))
        )
        await db.commit()
        deleted = result.rowcount or 0

    logger.info(
        "delete_job_with_descendants(%s): %d gelöscht (1 Parent + %d Repairs)",
        job_id, deleted, len(descendants),
    )
    return {"job": 1, "repairs": len(descendants), "total": deleted}
```

**Tests** (`tests/unit/test_retention_cascade.py`, neu):
- Single Job ohne Repairs → löscht 1 Row
- Job mit 1 Repair-Kind → löscht 2 Rows
- Job mit Repair-Kette (Repair-of-Repair-of-Repair) → löscht alle
- Job mit Geschwister-Repair (zwei Repairs vom selben Parent) → beide weg
- Nicht-existierender Job-ID → 0 Rows, kein Exception

**Pflicht-Anforderung:** Diese Funktion ist die einzige Stelle die Job-Rows
löschen darf. Keine direkten `DELETE FROM jobs WHERE ...` mehr in
neuen Code-Pfaden.

---

## Patch 2 — Auto-Retention `jobs_done` / `jobs_error`

**Ziel:** Periodischer Cleanup nach den definierten Fristen, mit Cascade.

**Subtiles Problem:** Ein Repair-Kind kann jünger sein als sein Parent.
Naiver `DELETE FROM jobs WHERE finished_at < cutoff` löscht den Parent,
lässt das Repair-Kind aber stehen → das Kind enthält in
`repair_input_json.final_prompt` den vollen Original-Text. Datenleak.

**Lösung:** "Root-first" — Retention prüft die Wurzel des Repair-Baums
(`parent_job_id IS NULL`). Ein Root läuft erst dann in Retention, wenn
**alle Descendants** ebenfalls über die Frist hinaus sind.

```python
async def cleanup_jobs_done() -> int:
    """Loescht abgeschlossene Job-Baeume nach RETENTION['jobs_done'].

    Cascade-Strategie 'newest-descendant':
      Ein Job-Baum (Root + Repair-Kinder) wird nur geloescht, wenn
      der JUENGSTE Eintrag im Baum aelter ist als der Cutoff. So bleibt
      ein frischer Repair-Job nicht ohne seinen Parent zurueck.
    """
    from app.core.database import async_session_factory
    from app.models.db import Job
    from sqlalchemy import select, func, and_
    from datetime import timezone as _tz

    cutoff = datetime.now(tz=_tz.utc) - timedelta(seconds=RETENTION["jobs_done"])
    deleted_jobs = 0

    async with async_session_factory() as db:
        # Kandidaten-Roots: parent_job_id IS NULL, status=done.
        # Wir holen IDs separat, um die Schleife transaktional zu halten.
        roots_q = select(Job.id).where(
            Job.parent_job_id.is_(None),
            Job.status == "done",
            Job.finished_at < cutoff,
        )
        root_ids = [row[0] for row in (await db.execute(roots_q))]

        for root_id in root_ids:
            # Juengstes finished_at im Baum (rekursiv) ermitteln
            # MERKE: rekursives CTE wuerde es eleganter machen, aber wir
            # haben pro Wurzel max ~5 Eintraege - flat-loop reicht.
            youngest = await _youngest_finished_at_in_tree(db, root_id)
            if youngest is None or youngest < cutoff:
                # Ganzer Baum kann weg
                stats = await delete_job_with_descendants(root_id)
                deleted_jobs += stats["total"]

    if deleted_jobs:
        logger.info("Retention jobs_done: %d Jobs (inkl. Repairs) geloescht",
                    deleted_jobs)
    return deleted_jobs


async def _youngest_finished_at_in_tree(db, root_id: str):
    """Liefert das maximale finished_at im Repair-Baum von root_id."""
    from app.models.db import Job
    from sqlalchemy import select, func

    to_visit = [root_id]
    timestamps: list = []
    while to_visit:
        current = to_visit.pop()
        result = await db.execute(
            select(Job.finished_at, Job.id).where(
                (Job.id == current) | (Job.parent_job_id == current)
            )
        )
        for fin, jid in result:
            if jid != current:
                to_visit.append(jid)
            if fin:
                timestamps.append(fin)
    return max(timestamps) if timestamps else None
```

Analog `cleanup_jobs_error()` für `status='error'` mit längerer Frist.

**Aktivierung in `retention_task()`:**
```python
async def retention_task():
    INTERVAL = 6 * 3600
    while True:
        try:
            await cleanup_recordings_audio()
            await cleanup_uploads()
            await cleanup_inactive_style_embeddings()
            await cleanup_jobs_done()     # ← neu
            await cleanup_jobs_error()    # ← neu
            await cleanup_old_logs()
        except ...
```

**Achtung — Default-Frist `jobs_done = 1h` ist sehr aggressiv:**
Bestehende UI-Workflows zeigen Jobs auch länger als 1h an (Therapeut
kommt nach der Mittagspause zurück und will den Vormittags-Bericht
copy-pasten). Vor Aktivierung:

- [ ] Frist mit dem Klinik-Team abstimmen — empfohlen min. 24h für DONE
- [ ] Settings-Override in `core/config.py` exponieren
  (`JOBS_DONE_RETENTION_HOURS`)
- [ ] UI-Warnung/Hinweistext "Job wird in X Stunden automatisch gelöscht"
- [ ] Optional: Therapeut kann einen Job "pinnen" (`Job.pinned_at`-Spalte)
  → Pinned Jobs überleben Auto-Retention

**Tests** (`tests/unit/test_retention_cascade.py`):
- Job mit `finished_at < cutoff` und keinem Repair-Kind → wird gelöscht
- Job mit `finished_at < cutoff` aber Repair-Kind `finished_at > cutoff`
  → bleibt (BAUM-Bedingung greift)
- Repair-Job ohne Parent (orphan durch frühere fehlerhafte Löschung)
  → wird einzeln behandelt wie ein Root
- `jobs_error` mit eigener Frist

---

## Patch 3 — Therapeut-initiierter Hard-Delete

**Ziel:** Endpoint `DELETE /api/jobs/{id}/hard` der einen Job samt
Repair-Kette explizit löscht (im Unterschied zum bestehenden Cancel-DELETE).

**Datei:** `app/api/jobs.py`

```python
@router.delete("/jobs/{job_id}/hard")
async def hard_delete_job(
    job_id: str,
    current_user: str = Depends(get_current_user),
):
    """Loescht einen Job und alle Repair-Kinder. Nicht reversibel.
    Trigger fuer den Therapeuten bei DSGVO-Auskunfts-/Loeschungsantraegen.
    """
    parent = await _resolve_parent_job(job_id)
    if parent.get("status") in ("pending", "running"):
        raise HTTPException(409, "Job laeuft - erst abbrechen, dann loeschen")

    stats = await delete_job_with_descendants(job_id)
    # Auch aus dem in-memory Cache entfernen damit kein Phantom-Job uebrig bleibt
    job_queue._cache.pop(job_id, None)
    # Kinder via Cache-Scan
    for jid, state in list(job_queue._cache.items()):
        if state.parent_job_id == job_id:
            job_queue._cache.pop(jid, None)
    return stats
```

**Audit-Pflicht:** Hard-Delete muss in `audit.log` einen Eintrag schreiben:
```python
audit_log(
    "hard_delete_job",
    user=current_user,
    target_job_id=job_id,
    descendants_count=stats["repairs"],
)
```

**UI:** Reset-Button "+ Neuer Antrag" sollte nicht direkt Hard-Delete machen
— das löscht nur lokalen State. Hard-Delete bekommt einen eigenen Button mit
Bestätigungsdialog ("Auch alle X Überarbeitungs-Versionen löschen?").

---

## Patch 4 — Anonymisierung (optional, eigene Designentscheidung)

**Nur falls sysTelios Anonymisierungs-Funktion bekommt:**

`result_text`, `befund_text`, `akut_text` enthalten Patientenname-Initialen
(z.B. "Frau M."). Eine spätere Anonymisierung müsste auch in folgenden
Feldern den Klartext suchen und ersetzen:

- `Job.result_text`
- `Job.result_befund`
- `Job.result_akut`
- `Job.transcript_redacted` (falls vorhanden)
- **`Job.repair_input_json.final_prompt`** ← der kritische neue Fall in Phase C
- `Job.generation_telemetry` (Stage-1-Audit hat ggf. Snippets)

Die Logik liegt am besten in einer eigenen Datei
`app/services/anonymization.py` mit Tests. Cascade-Pflicht:
**Anonymisierung am Parent zieht Anonymisierung aller Repair-Kinder nach
sich** — sonst widerspricht der Repair-Klartext der Anonymisierung.

Diese Anforderung ist ein eigener Sprint und nicht Teil dieses Patches.

---

## Reihenfolge der Umsetzung

1. **Erst Patch 1** (Cascade-Helper) — Voraussetzung für alles andere
2. **Optional Patch 3** (Hard-Delete-Endpoint) — kleinster nützlicher Schritt,
   gibt dem Therapeuten ein Werkzeug ohne globale Retention scharfzuschalten
3. **Erst nach Klinik-Abstimmung der Frist: Patch 2** (Auto-Retention)
4. **Patch 4** wenn Anonymisierung kommt

Solange keiner der Patches aktiv ist: Job-Rows bleiben in der DB für
immer. Das ist eine bestehende Eigenschaft des Systems, kein neuer Defekt
durch Phase C.

---

## Verifikation nach Umsetzung

Smoke-Test (manuell auf Staging):

1. Job generieren, 2x Repair durchführen → 3 Rows in `jobs` (Root + 2 Children)
2. `DELETE /jobs/{root_id}/hard` → alle 3 Rows weg, `performance.log`
   und `audit.log` haben die Einträge
3. `SELECT COUNT(*) FROM jobs WHERE parent_job_id IS NOT NULL AND
   parent_job_id NOT IN (SELECT id FROM jobs)` muss IMMER 0 sein
   (keine Orphans). Falls > 0: Cascade-Logik hat einen Bug.
