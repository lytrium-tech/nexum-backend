#!/usr/bin/env bash
# ==============================================================================
# Nexum PostgreSQL Release Gate (CT101) Canonical Reset Script
# ==============================================================================
# Canonical CT101 path: /srv/lytrium/testing/nexum/release-gate/scripts/reset.sh
# Destructive operation: drops and re-creates public schema in nexum_release_gate_test
# Synthetic and disposable only - NEVER touches production data
# ==============================================================================

set -euo pipefail

# 1. In-Guest Destructive Opt-In Requirement
if [[ "${NEXUM_ALLOW_DESTRUCTIVE_TEST_DB:-}" != "1" ]]; then
    echo "ERROR: Safety violation! NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1 is required inside guest." >&2
    exit 1
fi

EXPECTED_HOSTNAME="lab"
GATE_DIR="/srv/lytrium/testing/nexum/release-gate"
MARKER_FILE="${GATE_DIR}/.vulcano-release-gate"
DB_NAME="nexum_release_gate_test"
DB_USER="nexum_release_gate_test"

# 2. Identity & Marker Safety Checks
CURRENT_HOST=$(hostname)
if [[ "${CURRENT_HOST}" != "${EXPECTED_HOSTNAME}" ]]; then
    echo "ERROR: Identity mismatch! Expected hostname '${EXPECTED_HOSTNAME}', found '${CURRENT_HOST}'." >&2
    exit 1
fi

if [[ ! -f "${MARKER_FILE}" ]]; then
    echo "ERROR: Missing Vulcano Release Gate environment marker at ${MARKER_FILE}!" >&2
    exit 1
fi

# 3. Reset Database Schema (strictly loopback 127.0.0.1)
echo "Resetting test database '${DB_NAME}' schema public..."

psql -h 127.0.0.1 -U "${DB_USER}" -d "${DB_NAME}" -v ON_ERROR_STOP=1 <<-EOSQL
    -- Verify environment, user, and role privileges within transaction
    DO \$\$
    DECLARE
        r RECORD;
    BEGIN
        IF current_database() != '${DB_NAME}' THEN
            RAISE EXCEPTION 'Safety check failed: current_database is %', current_database();
        END IF;
        IF current_user != '${DB_USER}' THEN
            RAISE EXCEPTION 'Safety check failed: current_user is %', current_user;
        END IF;
        SELECT rolsuper, rolcreatedb, rolcreaterole INTO r FROM pg_roles WHERE rolname = current_user;
        IF r.rolsuper THEN
            RAISE EXCEPTION 'Safety check failed: user has superuser privileges (rolsuper=True)';
        END IF;
        IF r.rolcreatedb THEN
            RAISE EXCEPTION 'Safety check failed: user has createdb privileges (rolcreatedb=True)';
        END IF;
        IF r.rolcreaterole THEN
            RAISE EXCEPTION 'Safety check failed: user has createrole privileges (rolcreaterole=True)';
        END IF;
    END
    \$\$;

    -- Terminate active backend connections to test DB (except this one)
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE datname = '${DB_NAME}' AND pid <> pg_backend_pid();

    -- Re-create clean empty public schema
    DROP SCHEMA IF EXISTS public CASCADE;
    CREATE SCHEMA public AUTHORIZATION ${DB_USER};
    GRANT ALL ON SCHEMA public TO ${DB_USER};
EOSQL

echo "[PASS] Schema reset complete for '${DB_NAME}' (empty, synthetic state)."
