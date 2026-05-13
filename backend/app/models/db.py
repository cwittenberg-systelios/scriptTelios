"""
Datenbankmodelle.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Job(Base):
    """
    Verarbeitungsjob (Transkription + Generierung).

    Persistiert in PostgreSQL – ueberlebt Pod-Neustarts und ermoeglicht multi-worker.
    Progress-Updates laufen in-memory (transient) und werden bei Abschluss persistiert.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workflow: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)

    # Progress (transient in-memory, persistiert bei Abschluss)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    progress_phase: Mapped[str] = mapped_column(String(128), default="")
    progress_detail: Mapped[str] = mapped_column(String(256), default="")

    # Ergebnisse
    result_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_befund: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_akut: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_file: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Metadaten
    therapeut_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    style_info_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-serialisiert


    # Qualitätsprüfung (optional, opt-in via Frontend-Checkbox).
    # Persistiert das QualityCheckResult als JSON; siehe app/services/quality_check.py.
    # NULL = kein Check ausgefuehrt (Default).
    quality_check_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # v19.1: Telemetrie der LLM-Generierung.
    # Felder (siehe app/services/llm.py::_compute_telemetry):
    #   raw_length, think_length, think_ratio,
    #   had_orphan_think_open, had_orphan_think_close,
    #   tokens_hit_cap, used_thinking_fallback, eval_count,
    #   retry_used, degraded, degraded_reason, original_telemetry
    # NULL = Job aus Pre-v19.1-Zeit oder nicht-LLM-Job.
    generation_telemetry: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Recording(Base):
    """
    P0-Aufnahme: Audiodatei + Transkript, persistent auf /workspace/recordings.
    Löschung läuft über externe Datenschutz-Prozesse (deleted_at Soft-Delete).

    v18: therapeut_id hinzugefügt (Migration läuft automatisch via init_db_migrations
    in database.py beim Server-Start). Jeder Therapeut sieht nur eigene Aufnahmen.
    Audio-Datei wird nach 24h gelöscht (retention.py), Transkript bleibt in DB.
    """

    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # v18: Therapeut-Zuordnung (nullable für Rückwärtskompatibilität mit
    # Aufnahmen die vor diesem Update erstellt wurden)
    therapeut_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)

    # uploading | transcribing | ready | error
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploading", index=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)


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


# v13: DOKUMENTTYPEN und DOKUMENTTYP_LABELS werden jetzt aus der zentralen
# WORKFLOWS-Liste in app/core/workflows.py abgeleitet (Single Source of Truth).
# Wenn ein Workflow ergaenzt/umbenannt wird: NUR dort aendern - hier passt sich
# automatisch an. Der Sync-Test in test_suite.py wacht ueber Konsistenz.
from app.core.workflows import WORKFLOWS

DOKUMENTTYPEN: list[str] = [w.key for w in WORKFLOWS]
DOKUMENTTYP_LABELS: dict[str, str] = {w.key: w.label for w in WORKFLOWS}


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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    # Originaltext des Beispiels
    text: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_file: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Semantischer Vektor (nomic-embed-text liefert 768 Dimensionen)
    embedding: Mapped[list[float]] = mapped_column(Vector(768), nullable=True)

    # Statische Anker-Beispiele werden immer eingeschlossen
    ist_statisch: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
