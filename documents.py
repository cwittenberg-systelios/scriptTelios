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
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:  # type: ignore[return]
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    """Tabellen anlegen (beim ersten Start). pgvector-Extension aktivieren."""
    async with engine.begin() as conn:
        # pgvector Extension aktivieren (wird bei SQLite ignoriert)
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:
            pass  # SQLite oder Extension bereits vorhanden
        await conn.run_sync(Base.metadata.create_all)
