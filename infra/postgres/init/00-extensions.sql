-- Runs once, on first initialisation of the postgres-data volume.
-- Schema changes belong in Alembic migrations (apps/api/migrations), not here.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;
-- pgvector, for Cortex Knowledge. The knowledge migration also creates it if missing.
CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
  EXECUTE format('ALTER DATABASE %I SET timezone TO %L', current_database(), 'UTC');
END
$$;
