"""
Nexum PostgreSQL Release Gate (CT101) Lifecycle Tool.

Provides safe, reusable local lifecycle control for CT101 Release Gate:
status, start, validate, reset, stop, run.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import os
import re
import signal
import socket
import ssl
import subprocess
import sys
import time
import typing
import urllib.parse
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import httpcore
import httpx
from cryptography import x509
from cryptography.x509.oid import ExtensionOID, NameOID

FORBIDDEN_HOST_PATTERNS = [
    r"supabase",
    r"46\.224\.214\.227",
    r"api\.nexum\.lytrium\.tech",
    r"nexum\.lytrium\.tech",
    r"lytrium-vps",
]

EXACT_VMID = 101
EXACT_NODE = "vulcano"
EXACT_IP = "192.168.0.245"
EXACT_HOSTNAME = "lab"
EXACT_SSH_ALIAS = "nexum-lab"
EXACT_GATE_PATH = "/srv/lytrium/testing/nexum/release-gate/"
EXACT_DB_NAME = "nexum_release_gate_test"
EXACT_DB_USER = "nexum_release_gate_test"
CANONICAL_MARKER_PATH = "/srv/lytrium/testing/nexum/release-gate/.vulcano-release-gate"
CANONICAL_RESET_ENTRYPOINT = "/srv/lytrium/testing/nexum/release-gate/scripts/reset.sh"
NODE_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")

# Independently verified Proxmox VE leaf certificate pinned identity
PINNED_PVE_CERT_SHA256 = "764DC128CD215374D00D26DE547930DDC924B3B36BBACC9F3371DB3C2F34395D"
PINNED_PVE_HOSTNAME = "vulcano.home.arpa"

VERIFIED_PVE_LEAF_CERT_PEM = """-----BEGIN CERTIFICATE-----
MIIFAjCCAuqgAwIBAgIBATANBgkqhkiG9w0BAQsFADB2MSQwIgYDVQQDDBtQcm94
bW94IFZpcnR1YWwgRW52aXJvbm1lbnQxLTArBgNVBAsMJDkyOTU0NGY4LWVmODkt
NGU1Ni05YWZiLTczYjIzM2ZkNDAzYTEfMB0GA1UECgwWUFZFIENsdXN0ZXIgTWFu
YWdlciBDQTAeFw0yNjA5MjMwMzQ4MTlaFw0yODA5MjIwMzQ4MTlaMF0xGTAXBgNV
BAsMEFBWRSBDbHVzdGVyIE5vZGUxJDAiBgNVBAoMG1Byb3htb3ggVmlydHVhbCBF
bnZpcm9ubWVudDEaMBgGA1UEAwwRdnVsY2Fuby5ob21lLmFycGEwggEiMA0GCSqG
SIb3DQEBAQUAA4IBDwAwggEKAoIBAQCjF6AIsj4s4FvmJnidwRKemmiksqeHfJ7x
PR/2WbuDj/94ATmS52G3jvLuYvK4KIQ4YBMN8u9177onKhuGtGPGS1NMi5GVgERc
7/7qubqDh1uYekYG77bqpAGNatbqzxEt08rW39P0xzb9uk8+lCjVb1KKk8564aUO
1GmaXaoldmD9CfelWMJOVhtyCXXwjhdOoCLb1GsrWDCM8yHi2VQraTpyQR4RcUNf
UfNHIfbYNoYUmUOozyKopf5xOpVPbbjYIGZJMsRrE8gY+LkVWbIWYCqK8/JGwGrA
SJQA1n2+YuUgRx3t7J6xFMYvl5anz3jmnH6EJP6cAYsqCM+ZYjS9AgMBAAGjgbMw
gbAwCQYDVR0TBAIwADATBgNVHSUEDDAKBggrBgEFBQcDATBOBgNVHREERzBFhwR/
AAABhxAAAAAAAAAAAAAAAAAAAAABgglsb2NhbGhvc3SHBMCoAPCCB3Z1bGNhbm+C
EXZ1bGNhbm8uaG9tZS5hcnBhMB0GA1UdDgQWBBShmcAKs0QqX34esvK0WCmSBPm7
jzAfBgNVHSMEGDAWgBT9WgwM3ccLC6oVqC1XFeMj3W+muzANBgkqhkiG9w0BAQsF
AAOCAgEAK1dGtjsdX+ASr5552Y0wQHpg0TulqtNfwEMN/+4EQosogBFPYsHtG+vj
eQCjYssIjc6myYOuZmtZKFOYr9flqOLNz65o5bJPmnAGkxbU4ihNKfWUw700bPGW
THa6UN4sR5nYReMQeWJh5MgGAzrefAinBAL551A0JusJRIlpfyTJs41TOY8iCoTe
gUBDNlVZuFpAUcZpiKHufLCU2o5C4OrPeDptqZfKJaBsVKQO9MUDvvx1l52R1quy
AdHoFKb2woqLVPaGKPMk43ECv18uBE35oxXKVVreFkwEpuIbbz650wKPqNBBrTDx
X6znz56RnVFE5jEqoI9EmUzePW0lHU3pxHhEKgZ76C8CgQXWTBr7NzOlMJvUgYwk
NRJwSv6/jiPu9yOKw2ouFoT4PhW0LNbqVdgraA7lJkeuVkQWiwAWsplK+PNelxXK
mywoGdWgkmCMCG5ZCtCMXd3NwYVOo1sXQukl5QxrUyfH0qHD11pZkShP3eaZhmlR
/VKPJrYWSXaV+30oOHcneIiQ8ezfDh/Z0YzGbHf0WcUa4H3XvuszmPDuwUsDZ/iR
wYrfU13XgeC9GzJqdiFOTB6OvnxhuE6TUMjxhsszr2hjGqaJyAEgfV9VMXTnAVso
U1zKXX4QOuh4hlQOlfKYkI3Ljm9XS3a4jcjPu4QojUXA9fgjaqY=
-----END CERTIFICATE-----"""

REMOTE_VALIDATION_COMMAND = """python3 -c "
import sys, subprocess, os

# 1. Canonical Vulcano Release Gate environment marker check
marker = '/srv/lytrium/testing/nexum/release-gate/.vulcano-release-gate'
if not os.path.isfile(marker):
    print('FAIL: Missing Vulcano Release Gate environment marker')
    sys.exit(11)

# 2. Local PostgreSQL check strictly against loopback (never Supabase)
try:
    cmd = [
        'psql', '-h', '127.0.0.1', '-U', 'nexum_release_gate_test', '-d', 'nexum_release_gate_test',
        '-t', '-A', '-c',
        'SELECT current_setting(\\'server_version\\'), current_database(), current_user, '
        '(SELECT rolsuper FROM pg_roles WHERE rolname = current_user), '
        '(SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user), '
        '(SELECT rolcreaterole FROM pg_roles WHERE rolname = current_user);'
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    out = proc.stdout.strip()
    parts = out.split('|')
    if len(parts) < 6:
        print(f'FAIL: Unexpected query output: {out}')
        sys.exit(12)
    ver, db, user, is_super, is_createdb, is_createrole = parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]

    if not ver.startswith('17.'):
        print(f'FAIL: Non-PostgreSQL-17 runtime detected: {ver}')
        sys.exit(13)

    if db != 'nexum_release_gate_test':
        print(f'FAIL: Invalid current_database: {db}')
        sys.exit(14)

    if user != 'nexum_release_gate_test':
        print(f'FAIL: Invalid current_user: {user}')
        sys.exit(15)

    if is_super.lower() in ('t', 'true'):
        print('FAIL: Test role has superuser privileges (rolsuper=True)')
        sys.exit(16)

    if is_createdb.lower() in ('t', 'true'):
        print('FAIL: Test role has createdb privileges (rolcreatedb=True)')
        sys.exit(17)

    if is_createrole.lower() in ('t', 'true'):
        print('FAIL: Test role has createrole privileges (rolcreaterole=True)')
        sys.exit(18)

    print('OK: Remote gate identity and health verified')
    sys.exit(0)
except subprocess.CalledProcessError as e:
    print(f'FAIL: psql execution failed: {e.stderr.strip() or e.stdout.strip()}')
    sys.exit(19)
except Exception as e:
    print(f'FAIL: Error during check: {e}')
    sys.exit(20)
" """


class SafetyViolationError(RuntimeError):
    """Raised when an operation violates Release Gate safety invariants."""

    pass


def get_env_var(name: str, default: str = "") -> str:
    """Read environment variable from os.environ, falling back on Windows to HKCU\\Environment."""
    val = os.environ.get(name)
    if val is not None and val.strip():
        return val.strip()

    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                reg_val, _ = winreg.QueryValueEx(key, name)
                if isinstance(reg_val, str) and reg_val.strip():
                    return reg_val.strip()
        except (OSError, FileNotFoundError):
            pass

    return default


@dataclass(frozen=True)
class ReleaseGateConfig:
    pve_api_url: str = ""
    pve_token_id: str = ""
    pve_token_secret: str = ""
    node: str = EXACT_NODE
    vmid: int = EXACT_VMID
    host_ip: str = EXACT_IP
    hostname: str = EXACT_HOSTNAME
    ssh_alias: str = EXACT_SSH_ALIAS
    gate_path: str = EXACT_GATE_PATH
    db_name: str = EXACT_DB_NAME
    db_user: str = EXACT_DB_USER
    allow_destructive: bool = False
    ca_cert_path: str = ""

    def __repr__(self) -> str:
        return (
            f"ReleaseGateConfig(node={self.node!r}, vmid={self.vmid}, "
            f"host_ip={self.host_ip!r}, hostname={self.hostname!r}, "
            f"ssh_alias={self.ssh_alias!r}, gate_path={self.gate_path!r}, "
            f"db_name={self.db_name!r}, db_user={self.db_user!r}, "
            f"allow_destructive={self.allow_destructive}, "
            f"ca_cert_path={self.ca_cert_path!r}, "
            f"pve_api_url={self.pve_api_url!r}, pve_token_id={self.pve_token_id!r}, "
            f"pve_token_secret='***REDACTED***')"
        )

    @classmethod
    def from_env(cls) -> ReleaseGateConfig:
        vmid_raw = get_env_var("NEXUM_LAB_VMID", str(EXACT_VMID))
        try:
            vmid = int(vmid_raw)
        except ValueError:
            raise SafetyViolationError(f"Invalid NEXUM_LAB_VMID: {vmid_raw!r}. Must be an integer.")

        allow_destr = get_env_var("NEXUM_ALLOW_DESTRUCTIVE_TEST_DB", "") == "1"
        ca_cert_path = get_env_var("NEXUM_PVE_CA_CERT", "") or get_env_var("SSL_CERT_FILE", "")

        return cls(
            pve_api_url=get_env_var("NEXUM_PVE_API_URL", ""),
            pve_token_id=get_env_var("NEXUM_PVE_TOKEN_ID", ""),
            pve_token_secret=get_env_var("NEXUM_PVE_TOKEN_SECRET", ""),
            node=get_env_var("NEXUM_LAB_NODE", EXACT_NODE) or EXACT_NODE,
            vmid=vmid,
            host_ip=get_env_var("NEXUM_LAB_HOST", EXACT_IP) or EXACT_IP,
            hostname=get_env_var("NEXUM_LAB_HOSTNAME", EXACT_HOSTNAME) or EXACT_HOSTNAME,
            ssh_alias=get_env_var("NEXUM_LAB_SSH_ALIAS", EXACT_SSH_ALIAS) or EXACT_SSH_ALIAS,
            gate_path=get_env_var("NEXUM_GATE_PATH", EXACT_GATE_PATH) or EXACT_GATE_PATH,
            db_name=get_env_var("NEXUM_GATE_DB_NAME", EXACT_DB_NAME) or EXACT_DB_NAME,
            db_user=get_env_var("NEXUM_GATE_DB_USER", EXACT_DB_USER) or EXACT_DB_USER,
            allow_destructive=allow_destr,
            ca_cert_path=ca_cert_path,
        )


def check_string_safety(value: str, context: str = "") -> None:
    if not value:
        return
    for pattern in FORBIDDEN_HOST_PATTERNS:
        if re.search(pattern, value, re.IGNORECASE):
            raise SafetyViolationError(
                f"SAFETY VIOLATION [{context}]: Target matches forbidden production pattern '{pattern}'. Operation aborted."
            )


def verify_safety_guards(
    config: ReleaseGateConfig,
    is_destructive: bool = False,
    confirm: bool = False,
) -> None:
    # 1. Exact VMID: strictly 101. Never touch CT103 or any other guest.
    if config.vmid != EXACT_VMID:
        raise SafetyViolationError(
            f"SAFETY VIOLATION: VMID {config.vmid} is strictly unauthorized. Only CT101 (VMID {EXACT_VMID}) is allowed."
        )

    # 2. Exact IP: strictly 192.168.0.245 (rejects 192.168.0.243 or any other IP)
    if config.host_ip != EXACT_IP:
        raise SafetyViolationError(
            f"SAFETY VIOLATION: Host IP '{config.host_ip}' is invalid. Must be strictly '{EXACT_IP}'."
        )

    # 3. Exact Hostname: strictly 'lab'
    if config.hostname != EXACT_HOSTNAME:
        raise SafetyViolationError(
            f"SAFETY VIOLATION: Hostname '{config.hostname}' is invalid. Must be strictly '{EXACT_HOSTNAME}'."
        )

    # 4. Exact SSH Alias: strictly 'nexum-lab'
    if config.ssh_alias != EXACT_SSH_ALIAS:
        raise SafetyViolationError(
            f"SAFETY VIOLATION: SSH alias '{config.ssh_alias}' is invalid. Must be strictly '{EXACT_SSH_ALIAS}'."
        )

    # 5. Exact Gate Path: strictly '/srv/lytrium/testing/nexum/release-gate/'
    if config.gate_path != EXACT_GATE_PATH:
        raise SafetyViolationError(
            f"SAFETY VIOLATION: Gate path '{config.gate_path}' is invalid. Must be strictly '{EXACT_GATE_PATH}'."
        )

    # 6. Exact DB name: strictly 'nexum_release_gate_test'
    if config.db_name != EXACT_DB_NAME:
        raise SafetyViolationError(
            f"SAFETY VIOLATION: Target DB '{config.db_name}' is invalid. Must be strictly '{EXACT_DB_NAME}'."
        )

    # 7. Exact DB User: strictly 'nexum_release_gate_test'
    if config.db_user != EXACT_DB_USER:
        raise SafetyViolationError(
            f"SAFETY VIOLATION: Target User '{config.db_user}' is invalid. Must be strictly '{EXACT_DB_USER}'."
        )

    # 8. HTTPS requirement for Proxmox API URL
    if config.pve_api_url and not config.pve_api_url.lower().startswith("https://"):
        raise SafetyViolationError(
            "SAFETY VIOLATION: NEXUM_PVE_API_URL must use HTTPS scheme ('https://'). Insecure HTTP is forbidden."
        )

    # 9. Node validation: must be a clean alphanumeric identifier and strictly the authoritative node
    if not NODE_NAME_PATTERN.match(config.node):
        raise SafetyViolationError(
            f"SAFETY VIOLATION: Node name '{config.node}' is unsafe or malformed."
        )
    if config.node != EXACT_NODE:
        raise SafetyViolationError(
            f"SAFETY VIOLATION: Node '{config.node}' is unauthorized. Must be strictly '{EXACT_NODE}'."
        )

    # 10. Reject production patterns in all target fields
    check_string_safety(config.pve_api_url, "pve_api_url")
    check_string_safety(config.host_ip, "host_ip")
    check_string_safety(config.hostname, "hostname")
    check_string_safety(config.ssh_alias, "ssh_alias")
    check_string_safety(config.db_name, "db_name")
    check_string_safety(config.db_user, "db_user")
    check_string_safety(config.gate_path, "gate_path")

    # 11. Destructive operation fail-closed checks
    if is_destructive:
        if not config.allow_destructive:
            raise SafetyViolationError(
                "SAFETY VIOLATION: Destructive operation rejected. NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1 is not set."
            )
        if not confirm:
            raise SafetyViolationError(
                "SAFETY VIOLATION: Destructive operation rejected. Missing explicit --confirm flag."
            )


def redact_secrets(text: str, secrets: Sequence[str]) -> str:
    res = text
    for sec in secrets:
        if sec and len(sec) >= 3:
            res = res.replace(sec, "***REDACTED***")
    return res


def verify_pve_leaf_cert(
    cert_der: bytes,
    expected_pin: str = PINNED_PVE_CERT_SHA256,
    expected_hostname: str = PINNED_PVE_HOSTNAME,
    now: datetime.datetime | None = None,
) -> None:
    """Strictly verify PVE leaf certificate pin, hostname, and validity dates."""
    if not cert_der:
        raise SafetyViolationError("PVE TLS verification failed: empty peer certificate.")

    # 1. SHA-256 Pin equality
    actual_pin = hashlib.sha256(cert_der).hexdigest().upper()
    cleaned_expected = expected_pin.replace(":", "").strip().upper()
    if actual_pin != cleaned_expected:
        raise SafetyViolationError(
            f"PVE TLS verification failed: Certificate pin mismatch. "
            f"Expected {cleaned_expected}, got {actual_pin}."
        )

    # Parse certificate using cryptography
    try:
        cert = x509.load_der_x509_certificate(cert_der)
    except Exception as exc:
        raise SafetyViolationError(f"PVE TLS verification failed: cannot parse certificate: {exc}")

    # 2. Validity dates
    check_time = now if now is not None else datetime.datetime.now(datetime.UTC)
    if check_time < cert.not_valid_before_utc:
        raise SafetyViolationError(
            f"PVE TLS verification failed: Certificate is not yet valid (valid from {cert.not_valid_before_utc})."
        )
    if check_time > cert.not_valid_after_utc:
        raise SafetyViolationError(
            f"PVE TLS verification failed: Certificate is expired (expired at {cert.not_valid_after_utc})."
        )

    # 3. Hostname match (SAN / CN)
    san_dns: list[str] = []
    try:
        san_ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        san_dns = [name.lower() for name in san_ext.value.get_values_for_type(x509.DNSName)]
    except x509.ExtensionNotFound:
        pass

    cn_names: list[str] = [
        str(attr.value).lower()
        for attr in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    ]

    expected_host_lower = expected_hostname.lower()
    if expected_host_lower not in san_dns and expected_host_lower not in cn_names:
        raise SafetyViolationError(
            f"PVE TLS verification failed: Certificate hostname mismatch. "
            f"Expected '{expected_hostname}', found SAN={san_dns}, CN={cn_names}."
        )


class PVESyncStream:
    """Wraps network stream to enforce leaf certificate pin verification upon TLS upgrade."""

    def __init__(
        self,
        stream: httpcore.NetworkStream,
        pinned_sha256: str = PINNED_PVE_CERT_SHA256,
        pinned_hostname: str = PINNED_PVE_HOSTNAME,
        expected_pin: str | None = None,
        expected_hostname: str | None = None,
    ) -> None:
        self._stream = stream
        self.pinned_sha256 = (expected_pin or pinned_sha256).replace(":", "").strip().upper()
        self.pinned_hostname = expected_hostname or pinned_hostname

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        tls_stream = self._stream.start_tls(ssl_context, server_hostname, timeout)
        sock = getattr(tls_stream, "_sock", None)
        ssl_obj = getattr(tls_stream, "get_extra_info", lambda k: None)("ssl_object") or sock
        if ssl_obj is not None:
            der = ssl_obj.getpeercert(True) if hasattr(ssl_obj, "getpeercert") else None
            if not der:
                raise SafetyViolationError("PVE TLS verification failed: empty peer certificate.")
            verify_pve_leaf_cert(
                der,
                expected_pin=self.pinned_sha256,
                expected_hostname=self.pinned_hostname,
            )
        return tls_stream

    def __getattr__(self, name: str) -> typing.Any:
        return getattr(self._stream, name)


PveNetworkStream = PVESyncStream


class PVENetworkBackend(httpcore.SyncBackend):
    """Custom backend that connects TCP to configured endpoint host/IP while preserving SNI."""

    def __init__(
        self,
        target_ip: str,
        target_port: int,
        pinned_sha256: str,
        pinned_hostname: str,
    ) -> None:
        super().__init__()
        self.target_ip = target_ip
        self.target_port = target_port
        self.pinned_sha256 = pinned_sha256
        self.pinned_hostname = pinned_hostname

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> httpcore.NetworkStream:
        connect_host = self.target_ip if host == self.pinned_hostname else host
        connect_port = self.target_port if host == self.pinned_hostname else port
        stream = super().connect_tcp(
            connect_host, connect_port, timeout, local_address, socket_options
        )
        return PVESyncStream(stream, self.pinned_sha256, self.pinned_hostname)


class PVETransport(httpx.HTTPTransport):
    """Secure transport anchoring trust to verified leaf PEM with strict chain and pin checks."""

    def __init__(
        self,
        target_ip: str,
        target_port: int,
        pinned_sha256: str = PINNED_PVE_CERT_SHA256,
        pinned_hostname: str = PINNED_PVE_HOSTNAME,
        leaf_cert_pem: str = VERIFIED_PVE_LEAF_CERT_PEM,
        ca_cert_path: str = "",
        **kwargs: typing.Any,
    ) -> None:
        ssl_ctx = ssl.create_default_context()
        if leaf_cert_pem:
            ssl_ctx.load_verify_locations(cadata=leaf_cert_pem)
            ssl_ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        if ca_cert_path:
            ssl_ctx.load_verify_locations(cafile=ca_cert_path)
        ssl_ctx.check_hostname = True
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED

        self.target_ip = target_ip
        self.target_port = target_port
        self.pinned_sha256 = pinned_sha256.replace(":", "").strip().upper()
        self.pinned_hostname = pinned_hostname

        super().__init__(verify=ssl_ctx, **kwargs)
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl_ctx,
            network_backend=PVENetworkBackend(
                target_ip=self.target_ip,
                target_port=self.target_port,
                pinned_sha256=self.pinned_sha256,
                pinned_hostname=self.pinned_hostname,
            ),
        )


PveNetworkStream = PVESyncStream
PveNetworkBackend = PVENetworkBackend


class ProxmoxClient:
    def __init__(
        self,
        config: ReleaseGateConfig,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.config = config
        self._custom_client = http_client

    def _get_ssl_context(self) -> ssl.SSLContext:
        ssl_ctx = ssl.create_default_context()
        if VERIFIED_PVE_LEAF_CERT_PEM:
            ssl_ctx.load_verify_locations(cadata=VERIFIED_PVE_LEAF_CERT_PEM)
            ssl_ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        if self.config.ca_cert_path:
            ssl_ctx.load_verify_locations(cafile=self.config.ca_cert_path)
        ssl_ctx.check_hostname = True
        ssl_ctx.verify_mode = ssl.CERT_REQUIRED
        return ssl_ctx

    def _get_client(self) -> httpx.Client:
        if self._custom_client is not None:
            return self._custom_client

        if not self.config.pve_api_url:
            raise SafetyViolationError(
                "Missing NEXUM_PVE_API_URL in environment. Proxmox API lifecycle control requires PVE endpoint."
            )

        parsed = urllib.parse.urlparse(self.config.pve_api_url)
        connect_host = parsed.hostname or ""
        connect_port = parsed.port or 8006

        if not connect_host:
            raise SafetyViolationError(
                f"Invalid NEXUM_PVE_API_URL '{self.config.pve_api_url}': host is missing."
            )

        check_string_safety(connect_host, "pve_api_url_host")

        transport = PVETransport(
            target_ip=connect_host,
            target_port=connect_port,
            pinned_sha256=PINNED_PVE_CERT_SHA256,
            pinned_hostname=PINNED_PVE_HOSTNAME,
            leaf_cert_pem=VERIFIED_PVE_LEAF_CERT_PEM,
            ca_cert_path=self.config.ca_cert_path,
        )
        return httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0),
        )

    def _get_base_url(self) -> str:
        if not self.config.pve_api_url:
            raise SafetyViolationError(
                "Missing NEXUM_PVE_API_URL in environment. Proxmox API lifecycle control requires PVE endpoint."
            )
        parsed = urllib.parse.urlparse(self.config.pve_api_url)
        port = parsed.port or 8006
        # Connect through verified hostname so SNI, Host header, and TLS validation match vulcano.home.arpa
        return f"https://{PINNED_PVE_HOSTNAME}:{port}/api2/json"

    def _get_headers(self) -> dict[str, str]:
        if not self.config.pve_token_id or not self.config.pve_token_secret:
            raise SafetyViolationError(
                "Missing Proxmox API token credentials. Both NEXUM_PVE_TOKEN_ID and NEXUM_PVE_TOKEN_SECRET must be set."
            )
        return {
            "Authorization": f"PVEAPIToken={self.config.pve_token_id}={self.config.pve_token_secret}"
        }

    def _handle_request_error(self, exc: Exception) -> SafetyViolationError:
        err_msg = redact_secrets(str(exc), [self.config.pve_token_secret])
        return SafetyViolationError(f"Proxmox API communication failure: {err_msg}")

    def get_status(self) -> str:
        base_url = self._get_base_url()
        endpoint = f"{base_url}/nodes/{self.config.node}/lxc/{self.config.vmid}/status/current"
        try:
            client = self._get_client()
            resp = client.get(endpoint, headers=self._get_headers())
            resp.raise_for_status()
            data = resp.json().get("data", {})
            return str(data.get("status", "unknown"))
        except SafetyViolationError:
            raise
        except Exception as exc:
            raise self._handle_request_error(exc) from None

    def start(self) -> dict:
        base_url = self._get_base_url()
        endpoint = f"{base_url}/nodes/{self.config.node}/lxc/{self.config.vmid}/status/start"
        try:
            client = self._get_client()
            resp = client.post(endpoint, headers=self._get_headers())
            resp.raise_for_status()
            return resp.json()
        except SafetyViolationError:
            raise
        except Exception as exc:
            raise self._handle_request_error(exc) from None

    def shutdown(self) -> dict:
        base_url = self._get_base_url()
        endpoint = f"{base_url}/nodes/{self.config.node}/lxc/{self.config.vmid}/status/shutdown"
        try:
            client = self._get_client()
            resp = client.post(endpoint, headers=self._get_headers())
            resp.raise_for_status()
            return resp.json()
        except SafetyViolationError:
            raise
        except Exception as exc:
            raise self._handle_request_error(exc) from None

    def wait_for_status(
        self,
        target_status: str,
        timeout: int = 60,
        interval: float = 1.0,
    ) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                current = self.get_status()
                if current == target_status:
                    return True
            except Exception:
                pass
            time.sleep(interval)
        return False


def wait_for_ssh_ready(
    config: ReleaseGateConfig,
    timeout: int = 60,
    interval: float = 2.0,
    runner: Callable = subprocess.run,
) -> bool:
    start_time = time.time()
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "StrictHostKeyChecking=yes",
        config.ssh_alias,
        "echo ready",
    ]
    while time.time() - start_time < timeout:
        try:
            res = runner(cmd, capture_output=True, text=True)
            if res.returncode == 0 and "ready" in res.stdout:
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def fetch_guest_db_password(
    config: ReleaseGateConfig,
    runner: Callable = subprocess.run,
) -> str:
    """Fetch test database password from CT101 root-owned .db_pass over strict SSH into memory only."""
    verify_safety_guards(config)
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=yes",
        config.ssh_alias,
        f"sudo -n cat {config.gate_path}.db_pass",
    ]
    try:
        res = runner(cmd, capture_output=True, text=True)
    except Exception as exc:
        raise SafetyViolationError(
            f"Failed to execute SSH command to retrieve test DB password: {exc}"
        ) from None

    if res.returncode != 0:
        err = (res.stderr or "").strip()
        raise SafetyViolationError(
            f"Failed to retrieve test DB password from CT101 over SSH (exit code {res.returncode}): {err}"
        )

    pwd = (res.stdout or "").strip()
    if not pwd:
        raise SafetyViolationError("Retrieved empty test DB password from CT101.")
    return pwd


def validate_remote_gate(
    config: ReleaseGateConfig,
    runner: Callable = subprocess.run,
) -> tuple[bool, str]:
    verify_safety_guards(config)
    # Remote validation runs as lytrium-test so the guest's .pgpass is active
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "StrictHostKeyChecking=yes",
        config.ssh_alias,
        f"sudo -n -u lytrium-test {REMOTE_VALIDATION_COMMAND}",
    ]
    try:
        res = runner(cmd, capture_output=True, text=True)
        out = ((res.stdout or "") + "\n" + (res.stderr or "")).strip()
        if res.returncode == 0 and "OK: Remote gate identity and health verified" in out:
            return True, "Remote gate identity and health verified"
        return False, out or f"Remote health check failed with exit code {res.returncode}"
    except Exception as exc:
        return False, str(exc)


class SSHTunnel:
    """Safe SSH tunnel forwarding local port to CT101 PostgreSQL loopback 127.0.0.1:5432."""

    def __init__(
        self,
        ssh_alias: str = EXACT_SSH_ALIAS,
        remote_host: str = "127.0.0.1",
        remote_port: int = 5432,
        local_host: str = "127.0.0.1",
        local_port: int = 5433,
        popen_runner: Callable = subprocess.Popen,
    ) -> None:
        self.ssh_alias = ssh_alias
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.local_host = local_host
        self.local_port = local_port
        self.popen_runner = popen_runner
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-N",
            "-L",
            f"{self.local_host}:{self.local_port}:{self.remote_host}:{self.remote_port}",
            self.ssh_alias,
        ]
        self.process = self.popen_runner(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(self) -> None:
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                    self.process.wait(timeout=2)
                except Exception:
                    pass
            finally:
                self.process = None

    def wait_ready(self, timeout: float = 10.0, interval: float = 0.2) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            if self.process is not None and self.process.poll() is not None:
                return False
            try:
                with socket.create_connection((self.local_host, self.local_port), timeout=1.0):
                    return True
            except (OSError, ConnectionRefusedError):
                time.sleep(interval)
        return False


def cmd_status(config: ReleaseGateConfig, client: ProxmoxClient) -> int:
    verify_safety_guards(config)
    try:
        status = client.get_status()
    except Exception as exc:
        err_msg = redact_secrets(str(exc), [config.pve_token_secret])
        print(
            f"ERROR: Failed to retrieve CT101 status from Proxmox API: {err_msg}",
            file=sys.stderr,
        )
        return 1

    print("=== Nexum Release Gate Status ===")
    print(f"Container:       CT{config.vmid} (node: {config.node})")
    print(f"LXC Status:      {status}")
    print(f"Authoritative:   {config.hostname} ({config.host_ip})")
    print(f"SSH Alias:       {config.ssh_alias}")
    print(f"Gate Path:       {config.gate_path}")
    print(f"Target Database: {config.db_name}")
    print(f"Target User:     {config.db_user}")
    print(f"Destructive Opt: {'ENABLED (1)' if config.allow_destructive else 'DISABLED (0)'}")
    return 0


def cmd_start(config: ReleaseGateConfig, client: ProxmoxClient) -> int:
    verify_safety_guards(config)
    print(f"Checking CT{config.vmid} status...")
    current = client.get_status()
    if current == "running":
        print(f"CT{config.vmid} is already running.")
        return 0

    print(f"Starting CT{config.vmid} via Proxmox API...")
    client.start()
    print("Waiting for CT101 to reach 'running' state...")
    if client.wait_for_status("running", timeout=60):
        print(f"CT{config.vmid} started successfully and is now running.")
        return 0
    print(
        f"ERROR: CT{config.vmid} failed to reach 'running' state within timeout.", file=sys.stderr
    )
    return 1


def cmd_stop(config: ReleaseGateConfig, client: ProxmoxClient) -> int:
    verify_safety_guards(config)
    print(f"Checking CT{config.vmid} status...")
    current = client.get_status()
    if current == "stopped":
        print(f"CT{config.vmid} is already stopped.")
        return 0

    print(f"Requesting graceful shutdown of CT{config.vmid} via Proxmox API...")
    client.shutdown()
    print("Waiting for CT101 to reach 'stopped' state...")
    if client.wait_for_status("stopped", timeout=60):
        print(f"CT{config.vmid} stopped gracefully.")
        return 0
    print(f"ERROR: CT{config.vmid} failed to stop within timeout.", file=sys.stderr)
    return 1


def cmd_validate(
    config: ReleaseGateConfig,
    client: ProxmoxClient,
    runner: Callable = subprocess.run,
) -> int:
    verify_safety_guards(config)

    print("=== Nexum Release Gate Preflight Validation ===")
    print("[PASS] VMID is strictly CT101 (authorized guest).")
    print("[PASS] Host IP is strictly 192.168.0.245.")
    print("[PASS] Hostname is strictly 'lab'.")
    print("[PASS] SSH alias is strictly 'nexum-lab'.")
    print("[PASS] Canonical gate path is strictly '/srv/lytrium/testing/nexum/release-gate/'.")
    print("[PASS] Target Database is strictly 'nexum_release_gate_test'.")
    print("[PASS] Target User is strictly 'nexum_release_gate_test'.")
    print("[PASS] Proxmox API URL scheme is strictly HTTPS.")
    print("[PASS] Node path identifier is verified.")
    print("[PASS] Zero production identifiers detected (Supabase & VPS strictly banned).")

    if not config.pve_api_url or not config.pve_token_id or not config.pve_token_secret:
        print(
            "[WARN] Proxmox API credentials incomplete in local environment. Set NEXUM_PVE_API_URL, NEXUM_PVE_TOKEN_ID, NEXUM_PVE_TOKEN_SECRET for lifecycle actions."
        )
        return 1

    print("[PASS] Proxmox API credentials configured in environment.")
    try:
        status = client.get_status()
        print(f"[PASS] Proxmox API connection successful. CT101 status: {status}")
    except Exception as exc:
        print(f"[FAIL] Proxmox API connection check failed: {exc}")
        return 1

    return 0


def cmd_reset(
    config: ReleaseGateConfig,
    client: ProxmoxClient,
    confirm: bool,
    runner: Callable = subprocess.run,
) -> int:
    # 1. Fail closed if safety invariants or destructive opt-in fail
    verify_safety_guards(config, is_destructive=True, confirm=confirm)

    print("=== Nexum Release Gate Destructive Reset ===")
    print("Safety guards verified. Opt-in confirmed.")

    should_shutdown = False
    exit_code = 1
    try:
        current = client.get_status()
        if current != "running":
            print(f"CT{config.vmid} is {current}. Starting via Proxmox API for reset...")
            should_shutdown = True
            client.start()
            if not client.wait_for_status("running", timeout=60):
                print(
                    f"ERROR: CT{config.vmid} failed to reach 'running' state within timeout.",
                    file=sys.stderr,
                )
                return 1
        else:
            # CT101 must NEVER be left running when reset is invoked alone
            should_shutdown = True

        print(f"Waiting for SSH readiness on alias '{config.ssh_alias}'...")
        if not wait_for_ssh_ready(config, timeout=60, runner=runner):
            print("ERROR: Timed out waiting for SSH connectivity to CT101.", file=sys.stderr)
            return 1

        print("Performing remote gate identity validation before reset...")
        remote_ok, reason = validate_remote_gate(config, runner=runner)
        if not remote_ok:
            print(f"ERROR: Remote gate validation failed before reset: {reason}", file=sys.stderr)
            return 1

        # Execute canonical reset passing opt-in explicitly through sudo as lytrium-test
        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            config.ssh_alias,
            f"sudo -n -u lytrium-test env NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1 bash {CANONICAL_RESET_ENTRYPOINT}",
        ]
        print(f"Executing canonical reset on '{config.ssh_alias}': {CANONICAL_RESET_ENTRYPOINT}")
        proc = runner(cmd)
        exit_code = proc.returncode
        if exit_code == 0:
            print("Release Gate reset completed successfully.")
        else:
            print(
                f"ERROR: Reset execution returned non-zero code {exit_code}.",
                file=sys.stderr,
            )
    finally:
        if should_shutdown:
            print(
                "Release Gate: Reliability guarantee -> Requesting graceful CT101 shutdown after reset in finally..."
            )
            shutdown_ok = False
            try:
                client.shutdown()
                if client.wait_for_status("stopped", timeout=60):
                    print("Release Gate: CT101 shut down gracefully after reset.")
                    shutdown_ok = True
                else:
                    print(
                        "Release Gate: CRITICAL -> CT101 did not stop within timeout. Container may remain running!",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(
                    f"Release Gate: CRITICAL error during CT101 shutdown after reset: {exc}. Container may remain running!",
                    file=sys.stderr,
                )
            if not shutdown_ok:
                exit_code = 1 if exit_code == 0 else exit_code

    return exit_code


ALLOWED_CHILD_ENV_VARS = {
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "COMSPEC",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "WINDIR",
}


def cmd_run(
    config: ReleaseGateConfig,
    client: ProxmoxClient,
    command: Sequence[str],
    runner: Callable = subprocess.run,
    tunnel_class: type = SSHTunnel,
    tunnel_enabled: bool = True,
    local_port: int = 5433,
    destructive_opt_in: bool = False,
    password_fetcher: Callable[[ReleaseGateConfig, Callable], str] = fetch_guest_db_password,
) -> int:
    verify_safety_guards(config)

    if not command:
        print("ERROR: No command specified for release gate run wrapper.", file=sys.stderr)
        return 1

    # Install signal handlers to ensure finally triggers on termination
    def handle_signal(sig, frame):
        sys.exit(128 + sig)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle_signal)
        except (ValueError, AttributeError):
            pass

    print("=== Nexum Release Gate Test Wrapper ===")

    should_shutdown = False
    tunnel: SSHTunnel | None = None
    exit_code = 1
    db_pass = ""
    try:
        current = client.get_status()
        if current != "running":
            print(f"CT101 is currently {current}. Starting via Proxmox API...")
            should_shutdown = True
            client.start()
            if not client.wait_for_status("running", timeout=60):
                print(
                    "ERROR: CT101 failed to reach 'running' state within timeout.",
                    file=sys.stderr,
                )
                return 1
        else:
            should_shutdown = True

        print(f"Waiting for SSH readiness on alias '{config.ssh_alias}'...")
        if not wait_for_ssh_ready(config, timeout=60, runner=runner):
            print("ERROR: Timed out waiting for SSH connectivity to CT101.", file=sys.stderr)
            return 1

        print("Performing remote gate identity and health validation...")
        remote_ok, reason = validate_remote_gate(config, runner=runner)
        if not remote_ok:
            print(f"ERROR: Remote gate health validation failed: {reason}", file=sys.stderr)
            return 1

        # Fetch test database password over SSH into process memory (never persisted or printed)
        db_pass = os.environ.get("NEXUM_GATE_DB_PASSWORD", "").strip()
        if not db_pass:
            print("Fetching test database credentials from CT101 into memory...")
            db_pass = password_fetcher(config, runner)

        # Build clean minimal explicit child environment (whitelisted variables only)
        test_env = {k: v for k, v in os.environ.items() if k in ALLOWED_CHILD_ENV_VARS}

        # Do NOT silently set destructive test opt-in; require explicit opt-in
        if config.allow_destructive or destructive_opt_in:
            test_env["NEXUM_ALLOW_DESTRUCTIVE_TEST_DB"] = "1"

        # Establish SSH tunnel if enabled
        if tunnel_enabled:
            # Fail closed if local port is already in use
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_sock:
                probe_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
                try:
                    probe_sock.bind(("127.0.0.1", local_port))
                except OSError:
                    raise SafetyViolationError(
                        f"SAFETY VIOLATION: Local port {local_port} is already in use. Cannot establish isolated SSH tunnel."
                    )

            print(
                f"Establishing SSH tunnel ({config.ssh_alias} -> 127.0.0.1:{local_port} to CT101 loopback 127.0.0.1:5432)..."
            )
            tunnel = tunnel_class(
                ssh_alias=config.ssh_alias,
                remote_host="127.0.0.1",
                remote_port=5432,
                local_host="127.0.0.1",
                local_port=local_port,
            )
            tunnel.start()
            if not tunnel.wait_ready(timeout=10.0):
                print(
                    "ERROR: Timed out waiting for SSH PostgreSQL tunnel socket readiness. Port bind failed.",
                    file=sys.stderr,
                )
                return 1

            # Configure exact test database URL into minimal child environment (URL-encoded password)
            encoded_pass = urllib.parse.quote(db_pass, safe="")
            test_db_url = (
                f"postgresql+asyncpg://{config.db_user}:{encoded_pass}@127.0.0.1:{local_port}/{config.db_name}"
            )
            test_env["ARGOS_DATABASE_URL"] = test_db_url
            test_env["DATABASE_URL"] = test_db_url
            print(
                f"Configured test database URL: postgresql+asyncpg://{config.db_user}:***@127.0.0.1:{local_port}/{config.db_name}"
            )

        print(f"CT101 verified and healthy. Executing test command: {' '.join(command)}")
        proc = runner(command, env=test_env, capture_output=True, text=True)
        exit_code = proc.returncode

        if exit_code == 0:
            print("Release Gate: Test command completed successfully (exit code 0).")
        else:
            sanitized_out = redact_secrets(
                proc.stdout or "", [db_pass, config.pve_token_secret]
            )
            sanitized_err = redact_secrets(
                proc.stderr or "", [db_pass, config.pve_token_secret]
            )
            lines = (sanitized_err or sanitized_out).strip().splitlines()
            tail_evidence = "\n".join(lines[-20:]) if lines else "No output recorded."
            print(
                f"Release Gate: Test command failed with exit code {exit_code}.\n"
                f"--- Sanitized Failure Evidence (Bounded) ---\n{tail_evidence}",
                file=sys.stderr,
            )
    except SafetyViolationError:
        raise
    except Exception as exc:
        err_msg = redact_secrets(str(exc), [db_pass, config.pve_token_secret])
        print(
            f"ERROR: Exception occurred during release gate run lifecycle: {err_msg}",
            file=sys.stderr,
        )
        raise SafetyViolationError(f"Release gate run lifecycle failed: {err_msg}") from None
    finally:
        if tunnel is not None:
            print("Closing SSH PostgreSQL tunnel...")
            try:
                tunnel.stop()
            except Exception as exc:
                print(f"Warning during tunnel stop: {exc}", file=sys.stderr)

        if should_shutdown:
            print(
                "Release Gate: Reliability guarantee -> Requesting graceful CT101 shutdown in finally..."
            )
            shutdown_ok = False
            try:
                client.shutdown()
                if client.wait_for_status("stopped", timeout=60):
                    print("Release Gate: CT101 shut down gracefully.")
                    shutdown_ok = True
                else:
                    print(
                        "Release Gate: CRITICAL -> CT101 did not stop within timeout. Container may remain running!",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(
                    f"Release Gate: CRITICAL error during CT101 shutdown in finally: {exc}. Container may remain running!",
                    file=sys.stderr,
                )
            if not shutdown_ok:
                exit_code = 1 if exit_code == 0 else exit_code

    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Nexum PostgreSQL Release Gate (CT101) Lifecycle CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # status
    subparsers.add_parser("status", help="Get CT101 status and configuration")

    # start
    subparsers.add_parser("start", help="Start CT101 via Proxmox API")

    # stop
    subparsers.add_parser("stop", help="Gracefully shut down CT101 via Proxmox API")

    # validate
    subparsers.add_parser(
        "validate",
        help="Validate safety invariants and environment configuration",
    )

    # reset
    reset_parser = subparsers.add_parser(
        "reset",
        help="Destructive reset of release gate test environment",
    )
    reset_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Explicit confirmation for destructive reset (also requires NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1)",
    )

    # run
    run_parser = subparsers.add_parser(
        "run",
        help="Run a test command with CT101 lifecycle wrap (guaranteed shutdown in finally)",
    )
    run_parser.add_argument(
        "--tunnel",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Enable SSH tunnel to CT101 PostgreSQL loopback (default: True)",
    )
    run_parser.add_argument(
        "--local-port",
        type=int,
        default=5433,
        help="Local port for SSH tunnel forwarding (default: 5433)",
    )
    run_parser.add_argument(
        "--destructive-opt-in",
        action="store_true",
        help="Explicit opt-in to set NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1 in the test process environment",
    )
    run_parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Command and arguments to execute",
    )

    args = parser.parse_args(argv)

    try:
        config = ReleaseGateConfig.from_env()
        client = ProxmoxClient(config)

        if args.subcommand == "status":
            return cmd_status(config, client)
        elif args.subcommand == "start":
            return cmd_start(config, client)
        elif args.subcommand == "stop":
            return cmd_stop(config, client)
        elif args.subcommand == "validate":
            return cmd_validate(config, client)
        elif args.subcommand == "reset":
            return cmd_reset(
                config,
                client,
                confirm=args.confirm,
            )
        elif args.subcommand == "run":
            cmd = args.command
            if cmd and cmd[0] == "--":
                cmd = cmd[1:]
            return cmd_run(
                config,
                client,
                command=cmd,
                tunnel_enabled=args.tunnel,
                local_port=args.local_port,
                destructive_opt_in=args.destructive_opt_in,
            )
        else:
            parser.print_help()
            return 1
    except SafetyViolationError as sve:
        print(f"SAFETY ERROR: {sve}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
