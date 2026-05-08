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

-- (aktuell keine - bei Bedarf hier erweitern)

-- ── D. Indizes ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS ix_jobs_workflow         ON jobs (workflow);
CREATE INDEX IF NOT EXISTS ix_jobs_status           ON jobs (status);

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
-- Tabellen, die in frueheren Pod-Generationen vom Cluster-Superuser oder
-- App-User angelegt wurden, werden hier auf 'systelios' umgehaengt.
-- Setzt voraus, dass aktueller User Mitglied von 'systelios' ist
-- (siehe runpod-start.sh: GRANT systelios TO ...).
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO systelios', r.tablename);
    END LOOP;
    FOR r IN SELECT sequence_name FROM information_schema.sequences
             WHERE sequence_schema = 'public' LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO systelios', r.sequence_name);
    END LOOP;
    -- Enum-Typen: pg_type.typtype='e' = Enum
    FOR r IN SELECT t.typname FROM pg_type t
             JOIN pg_namespace n ON n.oid = t.typnamespace
             WHERE n.nspname = 'public' AND t.typtype = 'e' LOOP
        EXECUTE format('ALTER TYPE public.%I OWNER TO systelios', r.typname);
    END LOOP;
END $$;

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
