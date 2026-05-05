"""
ALTERNATIVE database.py (EMPFOHLEN):

Da runpod-start.sh bereits ALLE Schema-Migrationen als Owner-User ausfuehrt
(siehe Bash-Heredoc-Block ab Zeile 235), sind die Python-Migrationen
redundant - und genau die Quelle deines Fehlers.

Diese Variante:
  - entfernt _MIGRATIONS / run_migrations komplett aus dem Standard-Pfad
  - behaelt nur den Owner-Check als Diagnose-Tool beim Start
  - run_migrations() bleibt als No-Op fuer Abwaertskompatibilitaet
    (init_db ruft es noch auf, falls du es spaeter doch wieder brauchst)

Wenn du die Migrationen NICHT in runpod-start.sh haben willst (z.B. in
Nicht-RunPod-Deployments), nutze stattdessen die Variante mit
DB_AUTO_FIX_OWNER=1 und einer separaten Migrations-Verbindung als Owner.
"""
import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {"ssl": False},
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async_session_factory = AsyncSessionLocal


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:  # type: ignore[return]
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """
    Tabellen anlegen (beim ersten Start), pgvector-Extension aktivieren.

    Schema-Migrationen werden NICHT mehr hier ausgefuehrt - sie laufen in
    runpod-start.sh als Owner-User (systelios). Diese init_db braucht nur
    DDL-Rechte fuer create_all (ungenutzt im laufenden Betrieb).
    """
    if "postgresql" in settings.DATABASE_URL:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.commit()
        except Exception:
            pass

    # create_all ist idempotent. Wenn der App-User keine CREATE-Rechte hat,
    # passiert nichts (Tabellen wurden bereits in runpod-start.sh angelegt).
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        logger.info(
            "create_all uebersprungen (vermutlich durch Bash-Migration "
            "bereits abgedeckt): %s", exc
        )

    # Diagnose-Owner-Check
    await _check_table_ownership()


# ── Owner-Check (Diagnose) ────────────────────────────────────────────────────
EXPECTED_OWNER = os.environ.get("DB_EXPECTED_OWNER")

_OWNER_CHECK_SQL = text("""
    SELECT tablename, tableowner
    FROM pg_tables
    WHERE schemaname = 'public'
      AND tableowner <> :expected_owner
    ORDER BY tablename
""")


async def _check_table_ownership() -> None:
    """Warnt bei Owner-Mismatch. Korrektur passiert in runpod-start.sh."""
    if not EXPECTED_OWNER or "postgresql" not in settings.DATABASE_URL:
        return
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                _OWNER_CHECK_SQL, {"expected_owner": EXPECTED_OWNER}
            )
            mismatches = [(row.tablename, row.tableowner) for row in result]
    except Exception as exc:
        logger.warning("Owner-Check fehlgeschlagen: %s", exc)
        return

    if mismatches:
        logger.warning(
            "Owner-Mismatch: %d Tabelle(n) gehoeren nicht '%s': %s. "
            "Naechster Pod-Restart heilt das automatisch (runpod-start.sh).",
            len(mismatches),
            EXPECTED_OWNER,
            ", ".join(f"{t}={o}" for t, o in mismatches),
        )


async def run_migrations() -> None:
    """
    No-op fuer Abwaertskompatibilitaet.

    Schema-Migrationen laufen in runpod-start.sh als Owner-User (systelios).
    Dort sind sie auch besser aufgehoben:
      - laufen vor dem Backend-Start (kein Race)
      - laufen mit DDL-Rechten (kein Permission-Problem)
      - sind im Server-Log und nicht im Backend-Log sichtbar
    """
    return
