"""
Datenbankmodelle.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, Integer, String, Text, JSON
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
    # Sprint B (Multi-Job-Liste P1): kompakte Patientenkennung fuer die
    # Job-Liste. Format wie aus dem Frontend uebergeben ("Frau M.", "Herr S.",
    # oder nur "M." ohne Anrede). NULL fuer Jobs ohne uebergebenen Namen
    # oder Jobs aus der Zeit vor diesem Feld.
    patient_kuerzel: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model_used: Mapped[str | None] = mapped_column(String(128), nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    style_info_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-serialisiert

    # v19.1: Telemetrie der LLM-Generierung.
    # Felder (siehe app/services/llm.py::_compute_telemetry):
    #   raw_length, think_length, think_ratio,
    #   had_orphan_think_open, had_orphan_think_close,
    #   tokens_hit_cap, used_thinking_fallback, eval_count,
    #   retry_used, degraded, degraded_reason, original_telemetry
    # NULL = Job aus Pre-v19.1-Zeit oder nicht-LLM-Job.
    generation_telemetry: Mapped[dict | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)

    # v19.2: Two-Stage-Pipeline – Stage 1 (Verlauf-Verdichtung).
    # verlauf_summary_text:   der von Stage 1 erzeugte verdichtete Text
    #                         (4-Abschnitt-Form: Sitzungsübersicht, Themen,
    #                         Interventionen, Entwicklung). NULL wenn Stage 1
    #                         nicht ausgelöst oder fehlgeschlagen ist.
    # verlauf_summary_audit:  JSONB mit Metadaten zur Stage-1-Ausfuehrung
    #                         (applied, raw_word_count, summary_word_count,
    #                         compression_ratio, duration_s, retry_used,
    #                         degraded, issues, fallback_reason, ...).
    #                         NULL wenn Workflow Stage 1 nicht beruehrt.
    verlauf_summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    verlauf_summary_audit: Mapped[dict | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)

    # v19.3: Repair-Kontext-Persistierung.
    # source_verlauf_text:        Roh-Verlauf nach clean_verlauf_text (NULL wenn
    #                             Workflow keine Verlaufsdoku nutzt). Wird beim
    #                             Repair als Fallback genutzt wenn keine
    #                             verlauf_summary_text vorliegt (z.B. weil
    #                             Verlauf < STAGE1_VERLAUF_MIN_WORDS).
    # transcript_summary_text:    Synthese des Transkripts nach Stage 1 (NULL
    #                             wenn nicht relevant oder nicht gelaufen).
    #                             Analog zu verlauf_summary_text.
    # source_antragsvorlage_text: Antragsvorlage nach extract_text. Wichtig
    #                             fuer akutantrag/verlaengerung/entlassbericht -
    #                             enthaelt Diagnosen, Anamnese, Status.
    # source_vorantrag_text:      Vorheriger Antrag bei folgeverlaengerung.
    #                             Enthaelt Verlauf der Vorphase + Anamnese
    #                             + Diagnosen aus der vorigen Antragstellung.
    # Hinweis: das Roh-Transkript steckt schon in result_transcript
    # (historisches Naming - bewusst nicht umbenannt um Migration klein zu halten).
    source_verlauf_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_antragsvorlage_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_vorantrag_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # v19.13: Prozessreflexion (P4, Abschlussreflexion des Klienten).
    # NULL wenn keine hochgeladen wurde oder Workflow != entlassbericht.
    # Repair-Fidelity-Quelle analog source_antragsvorlage_text.
    source_prozessreflexion_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # v19 Phase 1: QualityCheck-Ergebnis fuer den finalen result_text.
    # Format siehe app/services/quality_check.py::serialize_issues:
    #   {"version": 1, "workflow": ..., "issues": [...], "summary": {...}}
    # NULL = Job aus Pre-v19-Zeit oder Job ohne fertigen result_text.
    quality_check_json: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=True
    )

    # v19 Phase C: Repair-Beziehung. Ein Repair-Job ist ein vollwertiger
    # Eintrag in der Queue, hat aber einen Verweis auf den Original-Job.
    #   - parent_job_id:    FK auf jobs.id (indiziert, nullable). Bei mehreren
    #                       Repair-Runden zeigt es auf den DIREKTEN Vorgaenger.
    #   - repair_input_json: Snapshot des Therapeuten-Inputs zum Audit:
    #                       {accepted_issue_codes, user_hint, final_prompt}
    parent_job_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True
    )
    repair_input_json: Mapped[dict | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=True
    )


class Recording(Base):
    """
    P0-Aufnahme: Audiodatei + Transkript, persistent auf /workspace/recordings.
    Nutzer-Löschung via deleted_at (Soft-Delete); harte Löschung inkl.
    Transkript nach 90 Tagen durch retention.cleanup_recordings_db (§6a).

    v18: therapeut_id hinzugefügt (Migration läuft automatisch beim
    Server-Start via scripts/schema.sql). Jeder Therapeut sieht nur eigene Aufnahmen.
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
    # v19.16 (T4): Sekunden am Aufnahme-Ende ohne Transkript-Abdeckung
    # (NULL = vollstaendig). Migration: ADD COLUMN IF NOT EXISTS in schema.sql.
    coverage_gap_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)

    # uploading | transcribing | ready | error
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="uploading", index=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)




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
