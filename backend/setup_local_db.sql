-- Run this as postgres superuser:
-- psql -U postgres -f setup_local_db.sql

-- Create user if not exists
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'nxentra') THEN
        CREATE USER nxentra WITH PASSWORD 'mokat3a';
    END IF;
END
$$;

-- Create database if not exists
SELECT 'CREATE DATABASE nxentra OWNER nxentra'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'nxentra')\gexec

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE nxentra TO nxentra;

-- Connect to nxentra database and grant schema permissions
\c nxentra
GRANT ALL ON SCHEMA public TO nxentra;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO nxentra;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO nxentra;

-- Give nxentra user ability to create/drop tables (needed for migrations)
ALTER USER nxentra CREATEDB;

\echo 'Local database setup complete!'
\echo 'Update your .env file with: DATABASE_URL=postgres://nxentra:mokat3a@localhost:5432/nxentra'
