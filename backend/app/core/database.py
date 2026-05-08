"""
Datenbankverbindung (SQLAlchemy async + PostgreSQL).

Schema-Verwaltung:
  Alle DDL-Operationen (CREATE/ALTER TABLE, Indizes, Owner, Privilegien)
  passieren ausschliesslich in /workspace/scriptTelios/backend/scripts/schema.sql,
  ausgefuehrt von runpod-start.sh als 'systelios'.

  Die App verbindet als 'systelios_app' (DML-only) und macht KEIN DDL mehr.
  Insbesondere kein Base.metadata.create_all - das wuerde den App-User zum
  Owner neuer Tabellen machen und die User-Trennung kaputtmachen.

ENV (optional):
  DB_EXPECTED_OWNER   Erwarteter Owner aller App-Tabellen (z.B. 'systelios').
                      Wenn gesetzt, wird beim Start als Diagnose geprueft.
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

# Alias für Background-Tasks die ihre eigene Session erstellen müssen
async_session_factory = AsyncSessionLocal


class Base(DeclarativeBase):
    """SQLAlchemy-Base. Wird nur fuer Modell-Definitionen genutzt,
    NICHT mehr fuer create_all (siehe Modul-Docstring)."""
    pass


async def get_db() -> AsyncSession:  # type: ignore[return]
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """
    Beim Backend-Start aufrufen.

    Macht KEIN DDL mehr - das ist Aufgabe von runpod-start.sh + schema.sql.
    Hier nur:
      - Owner-Diagnose (Warnung im Log, falls Mismatch)
      - Sanity-Check, dass alle erwarteten Tabellen existieren
    """
    if "postgresql" not in settings.DATABASE_URL:
        return

    await _check_table_ownership()
    await _check_required_tables()


# ── Owner-Diagnose ────────────────────────────────────────────────────────────
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
    if not EXPECTED_OWNER:
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
            "Naechster Pod-Restart heilt das (runpod-start.sh + schema.sql).",
            len(mismatches),
            EXPECTED_OWNER,
            ", ".join(f"{t}={o}" for t, o in mismatches),
        )


# ── Sanity-Check: alle benoetigten Tabellen vorhanden? ───────────────────────
_REQUIRED_TABLES = ("jobs", "recordings", "style_profiles", "style_embeddings")

_TABLE_CHECK_SQL = text("""
    SELECT tablename FROM pg_tables
    WHERE schemaname = 'public' AND tablename = ANY(:names)
""")


async def _check_required_tables() -> None:
    """Logt eine klare Fehlermeldung, wenn schema.sql nicht eingespielt wurde."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                _TABLE_CHECK_SQL, {"names": list(_REQUIRED_TABLES)}
            )
            existing = {row.tablename for row in result}
    except Exception as exc:
        logger.error("Tabellen-Check fehlgeschlagen: %s", exc)
        return

    missing = set(_REQUIRED_TABLES) - existing
    if missing:
        logger.error(
            "Tabellen fehlen: %s. Bitte schema.sql via runpod-start.sh "
            "einspielen. Die App wird mit Fehlern reagieren.",
            sorted(missing),
        )
