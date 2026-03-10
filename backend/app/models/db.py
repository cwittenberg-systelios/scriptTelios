"""
Datenbankmodelle.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Job(Base):
    """Verarbeitungsjob (Transkription oder Generierung)."""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    workflow: Mapped[str] = mapped_column(
        Enum("dokumentation", "anamnese", "verlaengerung", "entlassbericht", name="workflow_enum"),
        nullable=False,
    )
    step: Mapped[str] = mapped_column(
        Enum("transcription", "extraction", "generation", name="step_enum"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Enum("pending", "running", "done", "error", name="status_enum"),
        default="pending",
    )

    # Ergebnis
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_file: Mapped[str | None] = mapped_column(String(512), nullable=True)  # Pfad
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Audit
    therapeut_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)


class StyleProfile(Base):
    """Stilprofil eines Therapeuten (aus hochgeladenen Beispieltexten)."""

    __tablename__ = "style_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    therapeut_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    # Extrahierte Stilmerkmale als Prompt-Fragment
    style_context: Mapped[str] = mapped_column(Text, nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(512), nullable=True)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
