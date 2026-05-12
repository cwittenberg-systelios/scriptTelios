"""
Drop-in-Ersatz für den Migrations-Block in app/core/database.py.

Kernideen:
  1. Jede Migration in EIGENER Transaktion -> Folgefehler werden verhindert
     (kein 'current transaction is aborted' mehr).
  2. Idempotente DDLs (IF NOT EXISTS) bleiben, aber Fehler werden granular
     pro Migration geloggt, nicht en bloc.
  3. Owner-Mismatch wird beim Start erkannt und (optional) automatisch
     korrigiert, wenn die Verbindung als Superuser/Owner moeglich ist.
  4. Permission-Errors werden klar von echten Schema-Fehlern getrennt
     -> einfacheres Debugging in den Logs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError, DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Migrationsdefinitionen
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Migration:
    name: str
    sql: str
    # Wenn True: Fehler dieser Migration brechen den Startup NICHT ab
    optional: bool = True


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        name="recordings.therapeut_id",
        sql="""
            ALTER TABLE recordings
            ADD COLUMN IF NOT EXISTS therapeut_id VARCHAR(128);
        """,
    ),
    Migration(
        name="recordings.therapeut_id_index",
        sql="""
            CREATE INDEX IF NOT EXISTS ix_recordings_therapeut_id
            ON recordings (therapeut_id);
        """,
    ),
    Migration(
        name="jobs.quality_check_json",
        sql="""
            ALTER TABLE jobs
            ADD COLUMN IF NOT EXISTS quality_check_json TEXT;
        """,
    ),
    # weitere Migrationen hier ergaenzen ...
)


# ---------------------------------------------------------------------------
# Owner-Check (optional, aber empfohlen)
# ---------------------------------------------------------------------------

# Erwarteter Owner aller App-Tabellen. Aus ENV, damit dev/prod unterschiedlich.
EXPECTED_OWNER = os.environ.get("DB_EXPECTED_OWNER")  # z.B. "scripttelios_owner"
AUTO_FIX_OWNER = os.environ.get("DB_AUTO_FIX_OWNER", "0") == "1"

OWNER_CHECK_SQL = text("""
    SELECT tablename, tableowner
    FROM pg_tables
    WHERE schemaname = 'public'
      AND tableowner <> :expected_owner
""")


async def check_table_ownership(engine: AsyncEngine) -> list[tuple[str, str]]:
    """Liefert Liste (tablename, current_owner) fuer Tabellen mit falschem Owner."""
    if not EXPECTED_OWNER:
        return []
    async with engine.connect() as conn:
        result = await conn.execute(
            OWNER_CHECK_SQL, {"expected_owner": EXPECTED_OWNER}
        )
        return [(row.tablename, row.tableowner) for row in result]


async def fix_table_ownership(
    engine: AsyncEngine, mismatches: list[tuple[str, str]]
) -> None:
    """Versucht, Owner aller betroffenen Tabellen auf EXPECTED_OWNER zu setzen.

    Erfordert, dass die Verbindung Superuser-Rechte hat ODER aktueller User
    Owner ist. Schlaegt das fehl, wird nur geloggt - kein Startup-Abbruch.
    """
    for tablename, current_owner in mismatches:
        sql = text(
            f'ALTER TABLE "{tablename}" OWNER TO "{EXPECTED_OWNER}"'
        )
        try:
            async with engine.begin() as conn:
                await conn.execute(sql)
            log.info(
                "Owner von %s korrigiert: %s -> %s",
                tablename, current_owner, EXPECTED_OWNER,
            )
        except (ProgrammingError, DBAPIError) as exc:
            log.warning(
                "Owner-Fix fuer %s fehlgeschlagen (%s -> %s): %s",
                tablename, current_owner, EXPECTED_OWNER, exc,
            )


# ---------------------------------------------------------------------------
# Migrations-Runner
# ---------------------------------------------------------------------------

async def run_migrations(engine: AsyncEngine) -> None:
    """Fuehrt alle Migrationen sequenziell in EIGENEN Transaktionen aus.

    Wichtig: Jede Migration laeuft in einem separaten engine.begin()-Block.
    Ein Fehler in Migration N beeinflusst Migration N+1 nicht mehr
    (kein 'InFailedSQLTransactionError' mehr).
    """
    # 1) Owner-Check vor den Migrationen
    if EXPECTED_OWNER:
        try:
            mismatches = await check_table_ownership(engine)
        except Exception as exc:  # noqa: BLE001 - Diagnose, kein Abbruch
            log.warning("Owner-Check fehlgeschlagen: %s", exc)
            mismatches = []

        if mismatches:
            log.warning(
                "Owner-Mismatch erkannt (%d Tabellen, erwartet=%s): %s",
                len(mismatches),
                EXPECTED_OWNER,
                ", ".join(f"{t}={o}" for t, o in mismatches),
            )
            if AUTO_FIX_OWNER:
                await fix_table_ownership(engine, mismatches)
            else:
                log.warning(
                    "DB_AUTO_FIX_OWNER=0 -> Owner wird NICHT automatisch "
                    "korrigiert. Manuell beheben oder ENV setzen."
                )

    # 2) Migrationen einzeln ausfuehren
    for migration in MIGRATIONS:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(migration.sql))
            log.info("Migration '%s' OK", migration.name)

        except ProgrammingError as exc:
            # Klassifiziere typische Faelle fuer bessere Logs
            msg = str(exc.orig) if exc.orig else str(exc)
            if "permission denied" in msg or "must be owner" in msg:
                log.warning(
                    "Migration '%s' uebersprungen (Rechte fehlen): %s",
                    migration.name, msg,
                )
            else:
                log.warning(
                    "Migration '%s' fehlgeschlagen (Schema-Fehler): %s",
                    migration.name, msg,
                )
            if not migration.optional:
                raise

        except DBAPIError as exc:
            log.warning(
                "Migration '%s' fehlgeschlagen (DB-Fehler): %s",
                migration.name, exc,
            )
            if not migration.optional:
                raise
