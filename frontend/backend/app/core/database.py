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
