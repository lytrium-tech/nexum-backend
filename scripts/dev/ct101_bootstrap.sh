#!/usr/bin/env bash
# ==============================================================================
# Nexum PostgreSQL Release Gate (CT101) Provisioning / Bootstrap Script
# ==============================================================================
# LOCAL ARTIFACT ONLY - DO NOT EXECUTE DIRECTLY OUTSIDE CT101
# Target: Strictly CT101 LXC (Hostname: lab, IP: 192.168.0.245)
# Engine: PostgreSQL 17 (loopback 127.0.0.1:5432 only)
# Path: /srv/lytrium/testing/nexum/release-gate/
# Role/DB: nexum_release_gate_test (NOSUPERUSER, NOCREATEDB, NOCREATEROLE)
# ==============================================================================

set -euo pipefail
umask 077

EXPECTED_HOSTNAME="lab"
EXPECTED_IP="192.168.0.245"
GATE_DIR="/srv/lytrium/testing/nexum/release-gate"
MARKER_FILE="${GATE_DIR}/.vulcano-release-gate"
RESET_SCRIPT="${GATE_DIR}/scripts/reset.sh"
DB_NAME="nexum_release_gate_test"
DB_USER="nexum_release_gate_test"
OS_USER="lytrium-test"

echo "=== Nexum CT101 Release Gate Bootstrap ==="

# 1. Identity Assertions (Fail closed on mismatch)
CURRENT_HOST=$(hostname)
if [[ "${CURRENT_HOST}" != "${EXPECTED_HOSTNAME}" ]]; then
    echo "ERROR: Identity mismatch! Expected hostname '${EXPECTED_HOSTNAME}', found '${CURRENT_HOST}'." >&2
    exit 1
fi

if ! ip addr show | grep -q "${EXPECTED_IP}"; then
    echo "ERROR: Identity mismatch! Authoritative IP '${EXPECTED_IP}' not configured on this host." >&2
    exit 1
fi

echo "[PASS] Identity confirmed: CT101 ${EXPECTED_HOSTNAME} (${EXPECTED_IP})"

# 2. Dedicated no-sudo OS User Setup
if ! id -u "${OS_USER}" >/dev/null 2>&1; then
    echo "Creating dedicated test OS user '${OS_USER}'..."
    useradd -m -s /bin/bash "${OS_USER}"
fi

# Ensure OS user has NO sudo privileges
if groups "${OS_USER}" | grep -E '\b(sudo|wheel|root)\b'; then
    echo "ERROR: Security violation! User '${OS_USER}' must not have sudo/wheel privileges." >&2
    exit 1
fi

# Fail closed if an unexpected sudoers file exists for the user (never delete silently)
if [[ -f "/etc/sudoers.d/${OS_USER}" ]]; then
    echo "ERROR: Security violation! Unexpected sudoers file '/etc/sudoers.d/${OS_USER}' exists. Aborting." >&2
    exit 1
fi

# 3. Directory Layout and Marker File
mkdir -p "${GATE_DIR}"/{scripts,incoming,working,logs}
chmod o+x /srv/lytrium /srv/lytrium/testing /srv/lytrium/testing/nexum
touch "${MARKER_FILE}"
chmod 644 "${MARKER_FILE}"
chown root:root "${MARKER_FILE}"
chown root:root "${GATE_DIR}" "${GATE_DIR}/scripts"
chmod 755 "${GATE_DIR}" "${GATE_DIR}/scripts"

# Grant lytrium-test write permissions strictly to operational work subdirectories
chown -R "${OS_USER}:${OS_USER}" "${GATE_DIR}"/{incoming,working,logs}
chmod 750 "${GATE_DIR}"/{incoming,working,logs}
echo "[PASS] Gate directory, root marker, and restricted work directories configured."

# 4. PostgreSQL 17 Installation from Debian Package Sources (Idempotent)
if ! command -v psql >/dev/null 2>&1 || ! psql --version | grep -q '17\.'; then
    echo "PostgreSQL 17 absent. Installing from Debian package repository..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y postgresql-17 postgresql-client-17
fi

PG_VER=$(psql --version | awk '{print $3}')
if [[ ! "${PG_VER}" =~ ^17\. ]]; then
    echo "ERROR: PostgreSQL 17 required. Found version: ${PG_VER}" >&2
    exit 1
fi

# Enforce loopback listening in postgresql.conf
PG_CONF=$(find /etc/postgresql/17 -name "postgresql.conf" 2>/dev/null | head -n 1 || true)
if [[ -n "${PG_CONF}" && -f "${PG_CONF}" ]]; then
    sed -i "s/^#\?listen_addresses\s*=.*/listen_addresses = '127.0.0.1'/" "${PG_CONF}"
fi

# Enable and restart service, then verify active state
systemctl enable postgresql
systemctl restart postgresql
if ! systemctl is-active --quiet postgresql; then
    echo "ERROR: PostgreSQL service failed to start." >&2
    exit 1
fi

# 5. Idempotent Role and Database Creation (Non-Superuser, No Elevated Privs)
TEST_PASS="${NEXUM_GATE_INITIAL_PASSWORD:-}"
if [[ -z "${TEST_PASS}" ]]; then
    TEST_PASS=$(openssl rand -hex 16 2>/dev/null || python3 -c "import secrets; print(secrets.token_hex(16))")
fi

# Save database password to root-owned protected file for in-memory SSH retrieval
DB_PASS_FILE="${GATE_DIR}/.db_pass"
echo -n "${TEST_PASS}" > "${DB_PASS_FILE}"
chmod 600 "${DB_PASS_FILE}"
chown root:root "${DB_PASS_FILE}"

# Use safe parameterized formatting to avoid SQL injection
sudo -u postgres psql -v ON_ERROR_STOP=1 <<-EOSQL
    DO \$\$
    BEGIN
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
            EXECUTE format('CREATE ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', '${DB_USER}', '${TEST_PASS}');
        ELSE
            EXECUTE format('ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', '${DB_USER}', '${TEST_PASS}');
        END IF;
    END
    \$\$;

    SELECT format('CREATE DATABASE %I OWNER %I', '${DB_NAME}', '${DB_USER}')
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec

    GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
EOSQL

# Save local credentials strictly in restricted .pgpass for lytrium-test
PGPASS_FILE="/home/${OS_USER}/.pgpass"
echo "127.0.0.1:5432:${DB_NAME}:${DB_USER}:${TEST_PASS}" > "${PGPASS_FILE}"
chmod 600 "${PGPASS_FILE}"
chown "${OS_USER}:${OS_USER}" "${PGPASS_FILE}"
unset TEST_PASS

# Verify service state with pg_isready as lytrium-test
if ! sudo -u "${OS_USER}" pg_isready -h 127.0.0.1 -p 5432 -d "${DB_NAME}"; then
    echo "ERROR: PostgreSQL loopback service verification failed for user '${OS_USER}'." >&2
    exit 1
fi

echo "[PASS] PostgreSQL 17 role and database '${DB_NAME}' provisioned (NOSUPERUSER, NOCREATEDB, NOCREATEROLE)."

# 6. Install Canonical Reset Entrypoint (Root-Owned)
cat > "${RESET_SCRIPT}" <<'EOF'
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
EOF
chmod 555 "${RESET_SCRIPT}"
chown root:root "${RESET_SCRIPT}"
echo "[PASS] Canonical reset script staged at ${RESET_SCRIPT} (root-owned, mode 555)."

# 7. In-Guest Idle Abandoned-Run Shutdown Fail-Safe
WATCHDOG_SCRIPT="${GATE_DIR}/scripts/idle_watchdog.sh"
cat > "${WATCHDOG_SCRIPT}" <<'EOF'
#!/usr/bin/env bash
# In-guest fail-safe: automatically shuts down CT101 if idle for > 30 minutes without active sessions
set -euo pipefail
umask 077

ACTIVE_SSH=$(ss -tn sport = :22 '( dport != :22 )' 2>/dev/null | grep -c ESTAB || true)
if [[ "${ACTIVE_SSH}" -eq 0 ]]; then
    IDLE_FILE="/tmp/.ct101_idle_since"
    NOW=$(date +%s)
    if [[ ! -f "${IDLE_FILE}" ]]; then
        echo "${NOW}" > "${IDLE_FILE}"
    else
        IDLE_SINCE=$(cat "${IDLE_FILE}")
        ELAPSED=$(( NOW - IDLE_SINCE ))
        if [[ "${ELAPSED}" -ge 1800 ]]; then
            logger -t ct101-watchdog "CT101 has been idle for ${ELAPSED}s without active SSH. Initiating fail-safe shutdown."
            rm -f "${IDLE_FILE}"
            shutdown -h now
        fi
    fi
else
    rm -f "/tmp/.ct101_idle_since"
fi
EOF
chmod 750 "${WATCHDOG_SCRIPT}"
chown root:root "${WATCHDOG_SCRIPT}"

# Install in root cron every 5 minutes if not present
CRON_JOB="*/5 * * * * ${WATCHDOG_SCRIPT} >/dev/null 2>&1"
( crontab -l 2>/dev/null | grep -F "${WATCHDOG_SCRIPT}" ) || ( crontab -l 2>/dev/null; echo "${CRON_JOB}" ) | crontab -

echo "[PASS] In-guest abandoned-run shutdown fail-safe installed."
echo "=== Bootstrap Completed Successfully ==="
