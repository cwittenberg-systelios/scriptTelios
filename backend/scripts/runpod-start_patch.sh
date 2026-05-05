#!/usr/bin/env bash
# =============================================================================
# PATCH fuer runpod-start.sh
# Ersetzt den Block "User + Datenbank + Extension sicherstellen" (Zeilen 220-230)
# UND fuegt App-User-Anlage + Default Privileges hinzu.
# =============================================================================
#
# WAS SICH AENDERT:
#   - Zwei User: 'systelios' (Owner) + 'systelios_app' (DML-only)
#   - 'systelios_app' bekommt SELECT/INSERT/UPDATE/DELETE auf alle Tabellen
#   - ALTER DEFAULT PRIVILEGES sorgt dafuer, dass kuenftige Tabellen
#     automatisch fuer 'systelios_app' freigegeben werden
#   - DDL-Migrationen im Bash-Block laufen weiter als 'systelios' (Owner)
#   - Python-App verbindet als 'systelios_app' -> kann keinen Owner-Drift
#     mehr verursachen
#
# ANPASSUNGEN AUSSERHALB dieses Blocks:
#   - .env: DATABASE_URL Username 'systelios' -> 'systelios_app'
#   - .env: zusaetzlich DB_EXPECTED_OWNER=systelios setzen
#   - .env: DB_AUTO_FIX_OWNER=0 (Default; App-User kann eh nichts fixen)
# =============================================================================

# ── User + Datenbank + Extension sicherstellen ─────────────────────────────
# Owner-User
su -m "$PG_USER" -c "psql -d postgres -tc \"SELECT 1 FROM pg_roles WHERE rolname='systelios'\"" 2>/dev/null | grep -q 1 \
    || su -m "$PG_USER" -c "psql -d postgres -c \"CREATE USER systelios WITH PASSWORD 'systelios';\""

# App-User (NEU) — nur DML, kein DDL/Owner
su -m "$PG_USER" -c "psql -d postgres -tc \"SELECT 1 FROM pg_roles WHERE rolname='systelios_app'\"" 2>/dev/null | grep -q 1 \
    || su -m "$PG_USER" -c "psql -d postgres -c \"CREATE USER systelios_app WITH PASSWORD '${SYSTELIOS_APP_PASSWORD:-systelios_app}';\""

# Datenbank
su -m "$PG_USER" -c "psql -d postgres -tc \"SELECT 1 FROM pg_database WHERE datname='systelios'\"" 2>/dev/null | grep -q 1 \
    || su -m "$PG_USER" -c "psql -d postgres -c \"CREATE DATABASE systelios OWNER systelios;\""

# Extension
su -m "$PG_USER" -c "psql -d systelios -c \"CREATE EXTENSION IF NOT EXISTS vector;\"" >/dev/null 2>&1 \
    && echo "${OK}pgvector aktiviert" \
    || echo "${WARN}pgvector konnte nicht aktiviert werden"

# ── Owner-Drift heilen + App-User-Privilegien (NEU) ────────────────────────
# Laeuft bei JEDEM Start, ist idempotent. Heilt automatisch Tabellen, deren
# Owner durch frueheren Restore/UID-Wechsel verschoben wurde.
su -m "$PG_USER" -c "psql -d systelios -v ON_ERROR_STOP=0" <<'SQL' >/dev/null 2>&1
-- 1) Alle Tabellen/Sequenzen im public-Schema gehoeren 'systelios'
DO $$
DECLARE r record;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
        EXECUTE format('ALTER TABLE public.%I OWNER TO systelios', r.tablename);
    END LOOP;
    FOR r IN SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema='public' LOOP
        EXECUTE format('ALTER SEQUENCE public.%I OWNER TO systelios', r.sequence_name);
    END LOOP;
END $$;

-- 2) App-User: Schema-Zugriff
GRANT USAGE ON SCHEMA public TO systelios_app;

-- 3) App-User: DML auf alles BESTEHENDE
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO systelios_app;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO systelios_app;

-- 4) App-User: DML automatisch auf alles KUENFTIGE, was 'systelios' anlegt
ALTER DEFAULT PRIVILEGES FOR ROLE systelios IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO systelios_app;
ALTER DEFAULT PRIVILEGES FOR ROLE systelios IN SCHEMA public
    GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO systelios_app;
SQL
echo "${OK}Owner=systelios, App-User=systelios_app, Default-Privileges gesetzt"
