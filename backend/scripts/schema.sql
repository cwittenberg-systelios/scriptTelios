-- =============================================================================
-- scriptTelios - Datenbank-Schema (Single Source of Truth)
-- =============================================================================
-- Wird von runpod-start.sh ausgefuehrt:
--   1. als Cluster-Superuser (systelios_pg)
--   2. mit SET ROLE systelios -> alle CREATE/ALTER laufen unter Owner-Identitaet
--
-- Idempotent: bei jedem Pod-Start ausfuehrbar, ohne Schaden anzurichten.
-- Neue Schema-Aenderungen IMMER hier ergaenzen, niemals in Python.
--
-- Reihenfolge:
--   A. Extension(s)
--   B. Tabellen (CREATE TABLE IF NOT EXISTS)
--   C. Spalten-Migrationen (ALTER TABLE ... ADD COLUMN IF NOT EXISTS)
--   D. Indizes (CREATE INDEX IF NOT EXISTS)
--   E. Owner-Reparatur (sicherheitshalber, falls Tabelle schon mit
--      falschem Owner existierte)
--   F. Privilegien fuer App-User
-- =============================================================================

-- ── A. Extensions ──────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;

-- ── A2. Enum-Typen ─────────────────────────────────────────────────────────
-- dokumenttyp_enum wird von SQLAlchemy beim ersten create_all() angelegt.
-- Da wir kein create_all mehr machen, muessen wir den Typ hier definieren.
-- IF NOT EXISTS gibt es bei CREATE TYPE nicht, daher DO-Block.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'dokumenttyp_enum') THEN
        CREATE TYPE public.dokumenttyp_enum AS ENUM (
            'dokumentation',
            'anamnese',
            'verlaengerung',
            'folgeverlaengerung',
            'akutantrag',
            'entlassbericht'
        );
    END IF;
END $$;

-- ── B. Tabellen ────────────────────────────────────────────────────────────

-- Job: Verarbeitungsjob (Transkription + Generierung)
CREATE TABLE IF NOT EXISTS jobs (
    id                 VARCHAR(36)              PRIMARY KEY,
    created_at         TIMESTAMPTZ              NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ              NOT NULL DEFAULT now(),
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,
    workflow           VARCHAR(64)              NOT NULL,
    description        VARCHAR(512)             NOT NULL DEFAULT '',
    status             VARCHAR(20)              NOT NULL DEFAULT 'pending',
    cancel_requested   BOOLEAN                  NOT NULL DEFAULT FALSE,
    progress           INTEGER                  NOT NULL DEFAULT 0,
    progress_phase     VARCHAR(128)             NOT NULL DEFAULT '',
    progress_detail    VARCHAR(256)             NOT NULL DEFAULT '',
    result_text        TEXT,
    result_transcript  TEXT,
    result_befund      TEXT,
    result_akut        TEXT,
    result_file        VARCHAR(512),
    error_msg          TEXT,
    therapeut_id       VARCHAR(128),
    model_used         VARCHAR(128),
    duration_s         DOUBLE PRECISION,
    style_info_json    TEXT
);

-- Recording: P0-Aufnahmen (Audio + Transkript)
CREATE TABLE IF NOT EXISTS recordings (
    id              SERIAL                   PRIMARY KEY,
    created_at      TIMESTAMPTZ              NOT NULL DEFAULT now(),
    deleted_at      TIMESTAMPTZ,
    therapeut_id    VARCHAR(128),
    label           VARCHAR(120),
    filename        VARCHAR(512)             NOT NULL,
    duration_s      DOUBLE PRECISION,
    transcript      TEXT,
    status          VARCHAR(20)              NOT NULL DEFAULT 'uploading',
    error_msg       TEXT
);

-- StyleProfile: aggregierte Stilmerkmale eines Therapeuten
CREATE TABLE IF NOT EXISTS style_profiles (
    id              VARCHAR(36)              PRIMARY KEY,
    therapeut_id    VARCHAR(128)             NOT NULL,
    created_at      TIMESTAMPTZ              NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ              NOT NULL DEFAULT now(),
    style_context   TEXT                     NOT NULL,
    source_file     VARCHAR(512),
    word_count      INTEGER
);

-- StyleEmbedding: Einzelner Beispieltext + Vektor
-- dokumenttyp ist ein Enum (siehe Block A2). Existierende Tabellen aus
-- frueheren SQLAlchemy-create_all()-Aufrufen verwenden bereits diesen Typ;
-- Dump-Restore und CREATE TABLE IF NOT EXISTS sind damit kompatibel.
CREATE TABLE IF NOT EXISTS style_embeddings (
    id              VARCHAR(36)              PRIMARY KEY,
    therapeut_id    VARCHAR(128)             NOT NULL,
    dokumenttyp     public.dokumenttyp_enum  NOT NULL,
    created_at      TIMESTAMPTZ              NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ              NOT NULL DEFAULT now(),
    text            TEXT                     NOT NULL,
    word_count      INTEGER,
    source_file     VARCHAR(512),
    embedding       vector(768),
    ist_statisch    BOOLEAN                  NOT NULL DEFAULT FALSE
);

-- ── C. Spalten-Migrationen (additiv, idempotent) ───────────────────────────
-- Hier landen kuenftig neue Spalten oder Anpassungen an bestehenden Tabellen.
-- IMMER ADD COLUMN IF NOT EXISTS bzw. ALTER TABLE IF EXISTS verwenden.

-- v19.1: Telemetrie der LLM-Generierung (Think-Block-Diagnose, Retry-Status).
-- JSONB wegen kuenftiger Auswertungen (think_ratio, tokens_hit_cap, retry_used,
-- degraded). NULL bedeutet "Job stammt aus Pre-v19.1-Zeit" - alle neuen
-- Jobs haben mindestens eine basale Telemetrie.
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS generation_telemetry JSONB;

-- v19.2: Two-Stage-Pipeline – Stage 1 (Verlauf-Verdichtung).
-- verlauf_summary_text:  TEXT der verdichteten Verlaufsdokumentation
--                        (4-Abschnitt-Form). NULL wenn Stage 1 nicht lief.
-- verlauf_summary_audit: JSONB mit Audit-Metadaten der Stage-1-Ausfuehrung.
--                        Enthaelt u.a.: applied, raw_word_count,
--                        summary_word_count, compression_ratio, duration_s,
--                        retry_used, degraded, issues, fallback_reason,
--                        telemetry.
--                        NULL wenn Workflow nicht zur Stage-1-Whitelist gehoert.
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS verlauf_summary_text TEXT;
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS verlauf_summary_audit JSONB;

-- v19.3: Repair-Kontext-Persistierung.
-- Repair-Calls brauchen die Original-Quellen, um inhaltliche Therapeuten-
-- Hinweise (z.B. "schreibe noch einen Absatz zum Paargespraech") sinnvoll
-- umsetzen zu koennen. Daher persistieren wir Roh- und Synthese-Versionen
-- von Verlauf und Transkript am Job. Plus die Antragsvorlage/Vorantrag,
-- die beim Akutantrag, Verlaengerung, Folgeverlaengerung und Entlassbericht
-- als Anamnese-/Diagnose-Quelle dienen.
--
-- Naming-Konvention:
--   source_*_text:    Roh-Version nach Preprocessing (clean_verlauf_text /
--                     Whisper / extract_text). Klein genug fuer direkten
--                     Kontext-Einbau wenn Stage-1 nicht lief (Stage-1 greift
--                     erst ab STAGE1_VERLAUF_MIN_WORDS=1500 bzw.
--                     STAGE1_TRANSCRIPT_MIN_WORDS=3500 Woertern).
--   *_summary_text:   Stage-1-Synthese. Wird beim Repair bevorzugt
--                     verwendet, falls vorhanden (klein und schon
--                     verdichtet → optimal als Kontext).
--
-- Bestehende Felder (Backwards-Compat):
--   verlauf_summary_text: bereits da (s.o.), wird unveraendert genutzt
--   result_transcript:    bereits da, ist faktisch das "source_transcript_text"
--                         (Naming-Inkonsistenz aus historischen Gruenden)
--
-- Neue Felder:
--   source_verlauf_text:         Roh-Verlauf nach clean_verlauf_text (war
--                                bisher in jeder Generierung neu extrahiert
--                                und nach Stage 1 verworfen). NULL wenn
--                                Workflow keine Verlaufsdoku nutzt.
--   transcript_summary_text:     Synthese des Transkripts nach Stage 1
--                                (analog zu verlauf_summary_text). NULL wenn
--                                Transkript-Stage-1 nicht lief.
--   source_antragsvorlage_text:  Antragsvorlage nach extract_text (NULL wenn
--                                Workflow keine nutzt). Enthaelt typisch
--                                Anamnese + Diagnosen + Status - bei Akutantrag
--                                die WICHTIGSTE Datenquelle.
--   source_vorantrag_text:       Vorantrag (vorheriger Bericht) bei
--                                Folgeverlaengerung. Enthaelt Verlauf der
--                                Vorphase. NULL fuer andere Workflows.
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS source_verlauf_text TEXT;
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS transcript_summary_text TEXT;
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS source_antragsvorlage_text TEXT;
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS source_vorantrag_text TEXT;

-- v19 Phase 1: QualityCheck-Ergebnis fuer den finalen result_text.
-- Format siehe app/services/quality_check.py::serialize_issues:
--   {"version": 1, "workflow": ..., "issues": [...], "summary": {...}}
-- NULL fuer Pre-v19-Jobs oder Jobs die keinen Text generiert haben.
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS quality_check_json JSONB;

-- v19 Phase C: Therapeut-in-the-Loop Repair.
-- parent_job_id:     FK auf jobs.id (DIREKTER Vorgaenger bei mehreren Runden).
--                    Bewusst KEIN harter FK-Constraint - Repair-Jobs sollen
--                    auch erhalten bleiben wenn Parent geretained/geloescht
--                    wurde. Cascade-Verhalten regelt retention.py.
-- repair_input_json: Snapshot des Therapeuten-Inputs fuer Audit:
--                    {accepted_issue_codes, user_hint, final_prompt,
--                     custom_final_prompt_used (bool)}
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS parent_job_id VARCHAR(36);
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS repair_input_json JSONB;

-- ── D. Indizes ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS ix_jobs_workflow         ON jobs (workflow);
CREATE INDEX IF NOT EXISTS ix_jobs_status           ON jobs (status);

-- v19 Phase C: Drill-down "alle Repairs zu Job X". Partieller Index spart
-- Platz, weil >95% der Jobs keinen Parent haben.
CREATE INDEX IF NOT EXISTS ix_jobs_parent_job_id
    ON jobs (parent_job_id) WHERE parent_job_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_recordings_status     ON recordings (status);
CREATE INDEX IF NOT EXISTS ix_recordings_therapeut_id
    ON recordings (therapeut_id) WHERE therapeut_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_recordings_created
    ON recordings (created_at DESC) WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS ix_style_profiles_therapeut_id
    ON style_profiles (therapeut_id);

CREATE INDEX IF NOT EXISTS ix_style_embeddings_therapeut_id
    ON style_embeddings (therapeut_id);
CREATE INDEX IF NOT EXISTS ix_style_embeddings_dokumenttyp
    ON style_embeddings (dokumenttyp);

-- ── E. Owner-Reparatur ─────────────────────────────────────────────────────
-- WICHTIG: Owner-Heilung wird NICHT mehr hier durchgefuehrt.
-- ALTER ... OWNER TO neuer_owner verlangt Mitgliedschaft in beiden Owner-Rollen.
-- 'systelios' (durch SET ROLE in runpod-start.sh) ist nicht Mitglied von
-- 'systelios_app' oder 'systelios_pg' -> Heilung wuerde still scheitern.
-- Loesung: runpod-start.sh fuehrt die Owner-Heilung als Cluster-Superuser
-- aus, BEVOR diese schema.sql geladen wird.

-- ── F. Privilegien fuer App-User ───────────────────────────────────────────
-- DML-only, kein DDL. Default-Privileges sorgen dafuer, dass kuenftige
-- Tabellen automatisch fuer systelios_app freigegeben werden.
GRANT USAGE ON SCHEMA public TO systelios_app;

-- Enum-Typen: USAGE noetig damit App-User in Spalten dieses Typs schreiben kann
GRANT USAGE ON TYPE public.dokumenttyp_enum TO systelios_app;

GRANT SELECT, INSERT, UPDATE, DELETE
    ON ALL TABLES IN SCHEMA public TO systelios_app;
GRANT USAGE, SELECT, UPDATE
    ON ALL SEQUENCES IN SCHEMA public TO systelios_app;

ALTER DEFAULT PRIVILEGES FOR ROLE systelios IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO systelios_app;
ALTER DEFAULT PRIVILEGES FOR ROLE systelios IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO systelios_app;
ALTER DEFAULT PRIVILEGES FOR ROLE systelios IN SCHEMA public
    GRANT USAGE ON TYPES TO systelios_app;
