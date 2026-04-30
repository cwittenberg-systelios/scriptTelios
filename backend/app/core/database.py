"""
Datenbankverbindung (SQLAlchemy async + SQLite).
Fuer Produktion: DATABASE_URL auf PostgreSQL umstellen.
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text

from app.core.config import settings

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
# (nicht die Request-Session nutzen die nach Request-Ende geschlossen wird)
async_session_factory = AsyncSessionLocal


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:  # type: ignore[return]
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Tabellen anlegen (beim ersten Start). pgvector-Extension aktivieren."""
    # pgvector in eigener Transaktion – Fehler darf init_db nicht abbrechen
    if "postgresql" in settings.DATABASE_URL:
        try:
            async with engine.connect() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                await conn.commit()
        except Exception:
            pass  # Extension bereits vorhanden oder nicht verfuegbar
    # Tabellen anlegen
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Inkrementelle Migrationen (idempotent, laufen bei jedem Start)
    await run_migrations()


# ── Inkrementelle Migrationen ─────────────────────────────────────────────────
#
# Werden bei JEDEM Server-Start ausgeführt, sind aber idempotent:
# ADD COLUMN IF NOT EXISTS schlägt nicht fehl wenn die Spalte schon existiert.
# Kein Alembic nötig für kleine Schemaänderungen.
#
# Neue Migrationen IMMER unten anhängen, niemals bestehende ändern.

_MIGRATIONS: list[tuple[str, str]] = [
    # v18: recordings.therapeut_id — Therapeuten-Zuordnung für P0-Aufnahmen.
    # nullable damit Aufnahmen die vor dem Update erstellt wurden weiter
    # zugänglich bleiben (werden jedem Therapeut angezeigt der sie sehen will,
    # bis sie manuell gelöscht werden).
    (
        "recordings.therapeut_id",
        """
        ALTER TABLE recordings
        ADD COLUMN IF NOT EXISTS therapeut_id VARCHAR(128);
        """,
    ),
    (
        "recordings.therapeut_id_index",
        """
        CREATE INDEX IF NOT EXISTS ix_recordings_therapeut_id
        ON recordings (therapeut_id);
        """,
    ),
]


async def run_migrations() -> None:
    """
    Führt ausstehende Schemamigrationan aus.
    Idempotent: ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
    schlagen nicht fehl wenn bereits vorhanden.
    Nur für PostgreSQL aktiv — SQLite ignoriert IF NOT EXISTS nicht immer.
    """
    import logging
    logger = logging.getLogger(__name__)
    if "postgresql" not in settings.DATABASE_URL:
        return
    async with engine.begin() as conn:
        for name, sql in _MIGRATIONS:
            try:
                await conn.execute(text(sql.strip()))
                logger.debug("Migration OK: %s", name)
            except Exception as e:
                # Fehler loggen aber nicht abbrechen — beim nächsten Start
                # wird es erneut versucht
                logger.warning("Migration '%s' fehlgeschlagen (ignoriert): %s", name, e)
