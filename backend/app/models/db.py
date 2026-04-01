"""
Datenbankmodelle.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

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
        Enum("dokumentation", "anamnese", "verlaengerung", "folgeverlaengerung", "entlassbericht", name="workflow_enum"),
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


# Dokumenttypen als Konstante (Single Source of Truth)
DOKUMENTTYPEN = ["dokumentation", "anamnese", "verlaengerung", "folgeverlaengerung", "entlassbericht"]
DOKUMENTTYP_LABELS = {
    "dokumentation":        "Gesprächsdokumentation",
    "anamnese":             "Anamnese",
    "verlaengerung":        "Verlängerungsantrag",
    "folgeverlaengerung":   "Folgeverlängerungsantrag",
    "entlassbericht":       "Entlassbericht",
}


class StyleEmbedding(Base):
    """
    Einzelner Beispieltext eines Therapeuten mit Vektor-Embedding.

    - therapeut_id + dokumenttyp filtern die Suche
    - embedding ermöglicht semantische Ähnlichkeitssuche (pgvector)
    - ist_statisch = True: wird immer in den Prompt eingeschlossen (Anker-Beispiel)
    - ist_statisch = False: wird per Kosinus-Distanz zum Transkript ausgewählt
    """

    __tablename__ = "style_embeddings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    therapeut_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    dokumenttyp: Mapped[str] = mapped_column(
        Enum(*DOKUMENTTYPEN, name="dokumenttyp_enum"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Originaltext des Beispiels
    text: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Semantischer Vektor (nomic-embed-text liefert 768 Dimensionen)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=True)

    # Statische Anker-Beispiele werden immer eingeschlossen
    ist_statisch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
