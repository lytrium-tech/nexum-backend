"""
Unit tests for Nexum Release Gate Lifecycle Tool (scripts/dev/release_gate.py).

Verifies exact CT101 identity, HTTPS and node safety, live TLS pinned verification,
Windows HKCU environment fallback, fail-closed destructive operations, SSH strictness,
SSH tunnel lifecycle, minimal child environment isolation, secret redaction,
remote gate health checks with all role privileges, and guaranteed graceful CT101 shutdown.
"""

from __future__ import annotations

import datetime
import hashlib
import ssl
import subprocess
import urllib.parse
from collections.abc import Sequence
from unittest.mock import MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from scripts.dev.release_gate import (
    CANONICAL_RESET_ENTRYPOINT,
    EXACT_DB_NAME,
    EXACT_DB_USER,
    EXACT_GATE_PATH,
    EXACT_HOSTNAME,
    EXACT_IP,
    EXACT_SSH_ALIAS,
    EXACT_VMID,
    PINNED_PVE_CERT_SHA256,
    VERIFIED_PVE_LEAF_CERT_PEM,
    ProxmoxClient,
    PveNetworkStream,
    PVETransport,
    ReleaseGateConfig,
    SafetyViolationError,
    SSHTunnel,
    cmd_reset,
    cmd_run,
    cmd_status,
    fetch_guest_db_password,
    get_env_var,
    main,
    validate_remote_gate,
    verify_pve_leaf_cert,
    verify_safety_guards,
)


def make_clean_config(**kwargs) -> ReleaseGateConfig:
    defaults = {
        "pve_api_url": "https://192.168.0.200:8006/api2/json",
        "pve_token_id": "root@pam!nexum-token",
        "pve_token_secret": "secret-token-uuid-1234",
        "node": "vulcano",
        "vmid": EXACT_VMID,
        "host_ip": EXACT_IP,
        "hostname": EXACT_HOSTNAME,
        "ssh_alias": EXACT_SSH_ALIAS,
        "gate_path": EXACT_GATE_PATH,
        "db_name": EXACT_DB_NAME,
        "db_user": EXACT_DB_USER,
        "allow_destructive": False,
        "ca_cert_path": "",
    }
    defaults.update(kwargs)
    return ReleaseGateConfig(**defaults)


def make_test_cert_der(
    common_name: str = "vulcano.home.arpa",
    san_dns: Sequence[str] = ("vulcano.home.arpa",),
    days_valid: int = 30,
    expired: bool = False,
    future: bool = False,
) -> tuple[bytes, str]:
    """Generate in-memory test DER certificate and return (der_bytes, sha256_pin)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.UTC)
    if expired:
        not_before = now - datetime.timedelta(days=60)
        not_after = now - datetime.timedelta(days=1)
    elif future:
        not_before = now + datetime.timedelta(days=1)
        not_after = now + datetime.timedelta(days=60)
    else:
        not_before = now - datetime.timedelta(days=1)
        not_after = now + datetime.timedelta(days=days_valid)

    san_names = [x509.DNSName(d) for d in san_dns]
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectAlternativeName(san_names), critical=False)
    )
    cert = builder.sign(key, hashes.SHA256())
    der = cert.public_bytes(serialization.Encoding.DER)
    pin = hashlib.sha256(der).hexdigest().upper()
    return der, pin


# --- 1. EXACT CT101 IDENTITY & SAFETY GUARD TESTS ---


def test_verify_safety_guards_accepts_clean_default_config():
    config = make_clean_config()
    verify_safety_guards(config)


def test_verify_safety_guards_rejects_ip_192_168_0_243():
    config = make_clean_config(host_ip="192.168.0.243")
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert "Host IP '192.168.0.243' is invalid" in str(exc_info.value)
    assert "Must be strictly '192.168.0.245'" in str(exc_info.value)


@pytest.mark.parametrize("wrong_ip", ["192.168.0.1", "10.0.0.5", "127.0.0.1", "192.168.0.244"])
def test_verify_safety_guards_rejects_different_ip(wrong_ip: str):
    config = make_clean_config(host_ip=wrong_ip)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert f"Host IP '{wrong_ip}' is invalid" in str(exc_info.value)


@pytest.mark.parametrize("wrong_hostname", ["other-lab", "vulcano", "localhost", "dev-lab"])
def test_verify_safety_guards_rejects_different_hostname(wrong_hostname: str):
    config = make_clean_config(hostname=wrong_hostname)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert f"Hostname '{wrong_hostname}' is invalid" in str(exc_info.value)
    assert "Must be strictly 'lab'" in str(exc_info.value)


@pytest.mark.parametrize("wrong_alias", ["other-alias", "lab", "vulcano-ssh", "default"])
def test_verify_safety_guards_rejects_different_ssh_alias(wrong_alias: str):
    config = make_clean_config(ssh_alias=wrong_alias)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert f"SSH alias '{wrong_alias}' is invalid" in str(exc_info.value)
    assert "Must be strictly 'nexum-lab'" in str(exc_info.value)


@pytest.mark.parametrize("wrong_path", ["/tmp/gate/", "/srv/gate/", "/var/release-gate/"])
def test_verify_safety_guards_rejects_different_gate_path(wrong_path: str):
    config = make_clean_config(gate_path=wrong_path)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert f"Gate path '{wrong_path}' is invalid" in str(exc_info.value)
    assert "Must be strictly '/srv/lytrium/testing/nexum/release-gate/'" in str(exc_info.value)


@pytest.mark.parametrize("invalid_vmid", [100, 102, 103, 200])
def test_verify_safety_guards_rejects_vmid_not_101(invalid_vmid: int):
    config = make_clean_config(vmid=invalid_vmid)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert f"VMID {invalid_vmid} is strictly unauthorized" in str(exc_info.value)


@pytest.mark.parametrize(
    "forbidden_val,context",
    [
        ("https://my-project.supabase.co:8006", "pve_api_url"),
        ("db.pooler.supabase.com", "host_ip"),
        ("46.224.214.227", "host_ip"),
        ("api.nexum.lytrium.tech", "hostname"),
        ("lytrium-vps", "ssh_alias"),
    ],
)
def test_verify_safety_guards_rejects_forbidden_patterns(forbidden_val: str, context: str):
    kwargs = {context: forbidden_val}
    config = make_clean_config(**kwargs)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert "forbidden production pattern" in str(exc_info.value) or "is invalid" in str(
        exc_info.value
    )


@pytest.mark.parametrize("wrong_db", ["postgres", "nexum", "nexum_prod", "test_db"])
def test_verify_safety_guards_rejects_wrong_db_name(wrong_db: str):
    config = make_clean_config(db_name=wrong_db)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert "Target DB" in str(exc_info.value)
    assert "Must be strictly 'nexum_release_gate_test'" in str(exc_info.value)


@pytest.mark.parametrize("wrong_user", ["postgres", "root", "admin", "test_user"])
def test_verify_safety_guards_rejects_wrong_db_user(wrong_user: str):
    config = make_clean_config(db_user=wrong_user)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert "Target User" in str(exc_info.value)
    assert "Must be strictly 'nexum_release_gate_test'" in str(exc_info.value)


# --- 2. HTTPS SCHEME & NODE IDENTIFIER CHECKS ---


def test_verify_safety_guards_rejects_insecure_http_pve_url():
    config = make_clean_config(pve_api_url="http://192.168.0.200:8006/api2/json")
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert "must use HTTPS scheme" in str(exc_info.value)


@pytest.mark.parametrize("bad_node", ["../root", "node;rm", "vulcano/node", "node$"])
def test_verify_safety_guards_rejects_malformed_node_names(bad_node: str):
    config = make_clean_config(node=bad_node)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert "Node name" in str(exc_info.value)
    assert "unsafe or malformed" in str(exc_info.value)


@pytest.mark.parametrize("other_node", ["pve", "node1", "vulcano2", "other-node", "proxmox"])
def test_verify_safety_guards_rejects_unauthorized_node(other_node: str):
    config = make_clean_config(node=other_node)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config)
    assert f"Node '{other_node}' is unauthorized" in str(exc_info.value)
    assert "Must be strictly 'vulcano'" in str(exc_info.value)


# --- 3. PINNED TLS & LIVE STREAM VERIFICATION TESTS ---


def test_verified_pve_leaf_cert_matches_pinned_hash():
    cert = x509.load_pem_x509_certificate(VERIFIED_PVE_LEAF_CERT_PEM.strip().encode("ascii"))
    der = cert.public_bytes(serialization.Encoding.DER)
    calc_pin = hashlib.sha256(der).hexdigest().upper()
    assert calc_pin == PINNED_PVE_CERT_SHA256


def test_tls_verification_is_enabled_by_default():
    transport = PVETransport(target_ip="192.168.0.200", target_port=8006)
    ctx = transport._pool._ssl_context
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_tls_verification_uses_custom_ca_cert_when_provided():
    with patch("ssl.SSLContext.load_verify_locations") as mock_load:
        PVETransport(target_ip="192.168.0.200", target_port=8006, ca_cert_path="/path/to/ca.pem")
        mock_load.assert_called_with(cafile="/path/to/ca.pem")


def test_verify_pve_leaf_cert_success():
    der, pin = make_test_cert_der(
        common_name="vulcano.home.arpa",
        san_dns=["vulcano.home.arpa", "192.168.0.240"],
    )
    # Should succeed without error
    verify_pve_leaf_cert(
        der,
        expected_pin=pin,
        expected_hostname="vulcano.home.arpa",
    )


def test_verify_pve_leaf_cert_rejects_wrong_pin():
    der, _ = make_test_cert_der(common_name="vulcano.home.arpa")
    wrong_pin = "0000000000000000000000000000000000000000000000000000000000000000"
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_pve_leaf_cert(
            der,
            expected_pin=wrong_pin,
            expected_hostname="vulcano.home.arpa",
        )
    assert "Certificate pin mismatch" in str(exc_info.value)


def test_verify_pve_leaf_cert_rejects_wrong_hostname():
    der, pin = make_test_cert_der(
        common_name="other.domain.arpa",
        san_dns=["other.domain.arpa"],
    )
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_pve_leaf_cert(
            der,
            expected_pin=pin,
            expected_hostname="vulcano.home.arpa",
        )
    assert "Certificate hostname mismatch" in str(exc_info.value)


def test_verify_pve_leaf_cert_rejects_expired_cert():
    der, pin = make_test_cert_der(
        common_name="vulcano.home.arpa",
        san_dns=["vulcano.home.arpa"],
        expired=True,
    )
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_pve_leaf_cert(
            der,
            expected_pin=pin,
            expected_hostname="vulcano.home.arpa",
        )
    assert "Certificate is expired" in str(exc_info.value)


def test_verify_pve_leaf_cert_rejects_future_cert():
    der, pin = make_test_cert_der(
        common_name="vulcano.home.arpa",
        san_dns=["vulcano.home.arpa"],
        future=True,
    )
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_pve_leaf_cert(
            der,
            expected_pin=pin,
            expected_hostname="vulcano.home.arpa",
        )
    assert "Certificate is not yet valid" in str(exc_info.value)


def test_pve_network_stream_verifies_pin_on_live_tls_handshake():
    der, pin = make_test_cert_der(common_name="vulcano.home.arpa")

    mock_ssl_obj = MagicMock()
    mock_ssl_obj.getpeercert.return_value = der

    mock_tls_stream = MagicMock()
    mock_tls_stream.get_extra_info.return_value = mock_ssl_obj

    mock_raw_stream = MagicMock()
    mock_raw_stream.start_tls.return_value = mock_tls_stream

    stream = PveNetworkStream(mock_raw_stream, expected_pin=pin, expected_hostname="vulcano.home.arpa")
    res = stream.start_tls(ssl_context=MagicMock(), server_hostname="vulcano.home.arpa")
    assert res == mock_tls_stream
    mock_ssl_obj.getpeercert.assert_called_once_with(True)


def test_pve_network_stream_rejects_mismatched_pin_on_live_tls_handshake():
    der, _ = make_test_cert_der(common_name="vulcano.home.arpa")

    mock_ssl_obj = MagicMock()
    mock_ssl_obj.getpeercert.return_value = der

    mock_tls_stream = MagicMock()
    mock_tls_stream.get_extra_info.return_value = mock_ssl_obj

    mock_raw_stream = MagicMock()
    mock_raw_stream.start_tls.return_value = mock_tls_stream

    stream = PveNetworkStream(
        mock_raw_stream,
        expected_pin="0000000000000000000000000000000000000000000000000000000000000000",
        expected_hostname="vulcano.home.arpa",
    )
    with pytest.raises(SafetyViolationError) as exc_info:
        stream.start_tls(ssl_context=MagicMock(), server_hostname="vulcano.home.arpa")
    assert "Certificate pin mismatch" in str(exc_info.value)


# --- 4. WINDOWS HKCU ENVIRONMENT FALLBACK ---


def test_get_env_var_reads_os_environ_first():
    with patch.dict("os.environ", {"TEST_VAR_123": "my_val"}):
        assert get_env_var("TEST_VAR_123") == "my_val"


def test_get_env_var_falls_back_to_hkcu_when_not_in_environ():
    with patch.dict("os.environ", {}, clear=True):
        with patch("sys.platform", "win32"):
            mock_winreg = MagicMock()
            mock_key = MagicMock()
            mock_winreg.OpenKey.return_value.__enter__.return_value = mock_key
            mock_winreg.QueryValueEx.return_value = ("hkcu_secret_val", 1)

            with patch.dict("sys.modules", {"winreg": mock_winreg}):
                val = get_env_var("NEXUM_PVE_TOKEN_SECRET")
                assert val == "hkcu_secret_val"


# --- 5. STATUS EXITS NON-ZERO ON API FAILURE ---


def test_cmd_status_returns_nonzero_when_api_fails(capsys):
    secret = "secret-token-uuid-1234"
    config = make_clean_config(pve_token_secret=secret)
    mock_client = MagicMock()
    mock_client.get_status.side_effect = RuntimeError(f"API Connection refused with token {secret}")

    ret = cmd_status(config, mock_client)
    assert ret == 1
    err = capsys.readouterr().err
    assert "ERROR: Failed to retrieve CT101 status" in err
    assert secret not in err
    assert "***REDACTED***" in err


def test_cmd_status_success(capsys):
    config = make_clean_config()
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"

    ret = cmd_status(config, mock_client)
    assert ret == 0
    out = capsys.readouterr().out
    assert "CT101" in out
    assert "running" in out
    assert "192.168.0.245" in out


# --- 6. IN-MEMORY DB PASSWORD RETRIEVAL & REMOTE VALIDATION ---


def test_fetch_guest_db_password_success():
    config = make_clean_config()
    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(
        args=["ssh"], returncode=0, stdout="superSecretPassword123\n"
    )

    pwd = fetch_guest_db_password(config, runner=mock_runner)
    assert pwd == "superSecretPassword123"
    cmd_args = mock_runner.call_args[0][0]
    assert "StrictHostKeyChecking=yes" in cmd_args
    assert f"sudo -n cat {config.gate_path}.db_pass" in cmd_args[-1]


def test_fetch_guest_db_password_ssh_failure_never_prints_stdout():
    config = make_clean_config()
    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(
        args=["ssh"], returncode=1, stdout="leaked_stdout_secret", stderr="Permission denied\n"
    )

    with pytest.raises(SafetyViolationError) as exc_info:
        fetch_guest_db_password(config, runner=mock_runner)
    assert "Failed to retrieve test DB password from CT101" in str(exc_info.value)
    # Crucial: stdout must NEVER be printed on error
    assert "leaked_stdout_secret" not in str(exc_info.value)


def test_validate_remote_gate_checks_all_three_role_privileges():
    config = make_clean_config()
    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(
        args=["ssh"], returncode=0, stdout="OK: Remote gate identity and health verified\n"
    )

    ok, msg = validate_remote_gate(config, runner=mock_runner)
    assert ok is True
    assert "Remote gate identity and health verified" in msg
    cmd_args = mock_runner.call_args[0][0]
    assert "sudo -n -u lytrium-test" in cmd_args[-1]
    # Check that query checks rolsuper, rolcreatedb, rolcreaterole
    assert "rolsuper" in cmd_args[-1]
    assert "rolcreatedb" in cmd_args[-1]
    assert "rolcreaterole" in cmd_args[-1]


# --- 7. SSHTUNNEL STRICTNESS & PORT BIND VERIFICATION ---


def test_ssh_tunnel_command_includes_exit_on_forward_failure():
    mock_popen = MagicMock()
    tunnel = SSHTunnel(
        ssh_alias="nexum-lab",
        local_port=5433,
        remote_port=5432,
        popen_runner=mock_popen,
    )
    tunnel.start()

    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert "-o" in cmd
    assert "ExitOnForwardFailure=yes" in cmd
    assert "StrictHostKeyChecking=yes" in cmd


def test_ssh_tunnel_wait_ready_fails_when_port_cannot_be_bound():
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None
    mock_popen = MagicMock(return_value=mock_proc)

    tunnel = SSHTunnel(
        ssh_alias="nexum-lab",
        local_port=59999,
        popen_runner=mock_popen,
    )
    tunnel.start()

    with patch("socket.create_connection", side_effect=ConnectionRefusedError("Refused")):
        assert tunnel.wait_ready(timeout=0.2, interval=0.05) is False


# --- 8. RUN WRAPPER & CHILD ENVIRONMENT ISOLATION & OUTPUT CAPTURE ---


def test_cmd_run_sets_up_tunnel_and_cleans_up_on_success():
    config = make_clean_config()
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"
    mock_client.wait_for_status.return_value = True

    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(
        args=["pytest"], returncode=0, stdout="test success", stderr=""
    )

    mock_tunnel = MagicMock()
    mock_tunnel.wait_ready.return_value = True
    mock_tunnel_class = MagicMock(return_value=mock_tunnel)

    raw_password = "mypassword!@#$%"
    encoded_pass = urllib.parse.quote(raw_password, safe="")

    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=True):
        with patch("scripts.dev.release_gate.validate_remote_gate", return_value=(True, "OK")):
            with patch.dict(
                "os.environ",
                {
                    "NEXUM_PVE_TOKEN_ID": "tok",
                    "NEXUM_PVE_TOKEN_SECRET": "sec",
                    "NEXUM_GATE_DB_PASSWORD": raw_password,
                },
            ):
                ret = cmd_run(
                    config,
                    mock_client,
                    command=["pytest"],
                    runner=mock_runner,
                    tunnel_class=mock_tunnel_class,
                    tunnel_enabled=True,
                    local_port=5433,
                    destructive_opt_in=False,
                )

    assert ret == 0
    # Tunnel started and stopped
    mock_tunnel.start.assert_called_once()
    mock_tunnel.stop.assert_called_once()
    # CT101 shutdown called
    mock_client.shutdown.assert_called_once()

    # Check child environment
    env_passed = mock_runner.call_args[1]["env"]
    assert "NEXUM_PVE_TOKEN_ID" not in env_passed
    assert "NEXUM_PVE_TOKEN_SECRET" not in env_passed
    # Check that password is URL-encoded
    assert (
        env_passed["ARGOS_DATABASE_URL"]
        == f"postgresql+asyncpg://nexum_release_gate_test:{encoded_pass}@127.0.0.1:5433/nexum_release_gate_test"
    )
    # Destructive opt-in NOT set silently
    assert "NEXUM_ALLOW_DESTRUCTIVE_TEST_DB" not in env_passed


def test_cmd_run_captures_and_redacts_child_secrets_on_failure(capsys):
    config = make_clean_config()
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"
    mock_client.wait_for_status.return_value = True

    secret_pass = "ultraSecretPassword99"
    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(
        args=["pytest"],
        returncode=1,
        stdout="",
        stderr=f"Error connecting with password: {secret_pass}\nDetailed traceback...",
    )

    mock_tunnel = MagicMock()
    mock_tunnel.wait_ready.return_value = True
    mock_tunnel_class = MagicMock(return_value=mock_tunnel)

    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=True):
        with patch("scripts.dev.release_gate.validate_remote_gate", return_value=(True, "OK")):
            ret = cmd_run(
                config,
                mock_client,
                command=["pytest"],
                runner=mock_runner,
                tunnel_class=mock_tunnel_class,
                tunnel_enabled=True,
                password_fetcher=lambda cfg, r: secret_pass,
            )

    assert ret == 1
    err = capsys.readouterr().err
    assert "Failure Evidence" in err
    assert secret_pass not in err
    assert "***REDACTED***" in err


def test_cmd_run_minimal_child_env_omits_unrelated_secrets_and_production_endpoints():
    config = make_clean_config()
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"
    mock_client.wait_for_status.return_value = True

    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(args=["pytest"], returncode=0)

    mock_tunnel = MagicMock()
    mock_tunnel.wait_ready.return_value = True
    mock_tunnel_class = MagicMock(return_value=mock_tunnel)

    leak_env = {
        "SUPABASE_URL": "https://my-prod.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "supersecret-jwt-key",
        "VPS_SSH_KEY": "-----BEGIN OPENSSH PRIVATE KEY-----",
        "AWS_SECRET_ACCESS_KEY": "aws-secret-key-1234",
        "NEXUM_PVE_TOKEN_SECRET": "pve-token-secret-value",
        "PATH": "C:\\Windows\\System32;C:\\Python",
    }

    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=True):
        with patch("scripts.dev.release_gate.validate_remote_gate", return_value=(True, "OK")):
            with patch.dict("os.environ", leak_env, clear=True):
                ret = cmd_run(
                    config,
                    mock_client,
                    command=["pytest"],
                    runner=mock_runner,
                    tunnel_class=mock_tunnel_class,
                    tunnel_enabled=True,
                    password_fetcher=lambda cfg, r: "guestpass",
                )

    assert ret == 0
    child_env = mock_runner.call_args[1]["env"]
    assert "SUPABASE_URL" not in child_env
    assert "SUPABASE_SERVICE_ROLE_KEY" not in child_env
    assert "VPS_SSH_KEY" not in child_env
    assert "AWS_SECRET_ACCESS_KEY" not in child_env
    assert "NEXUM_PVE_TOKEN_SECRET" not in child_env
    assert "PATH" in child_env
    assert "ARGOS_DATABASE_URL" in child_env
    assert "guestpass" in child_env["ARGOS_DATABASE_URL"]


def test_cmd_run_passes_destructive_opt_in_when_explicitly_requested():
    config = make_clean_config()
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"
    mock_client.wait_for_status.return_value = True

    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(args=["pytest"], returncode=0)

    mock_tunnel = MagicMock()
    mock_tunnel.wait_ready.return_value = True
    mock_tunnel_class = MagicMock(return_value=mock_tunnel)

    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=True):
        with patch("scripts.dev.release_gate.validate_remote_gate", return_value=(True, "OK")):
            ret = cmd_run(
                config,
                mock_client,
                command=["pytest"],
                runner=mock_runner,
                tunnel_class=mock_tunnel_class,
                tunnel_enabled=True,
                local_port=5433,
                destructive_opt_in=True,
                password_fetcher=lambda cfg, r: "pass",
            )

    assert ret == 0
    env_passed = mock_runner.call_args[1]["env"]
    assert env_passed["NEXUM_ALLOW_DESTRUCTIVE_TEST_DB"] == "1"


def test_cmd_run_closes_tunnel_and_shuts_down_on_tunnel_failure():
    config = make_clean_config()
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"
    mock_client.wait_for_status.return_value = True

    mock_runner = MagicMock()
    mock_tunnel = MagicMock()
    mock_tunnel.wait_ready.return_value = False
    mock_tunnel_class = MagicMock(return_value=mock_tunnel)

    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=True):
        with patch("scripts.dev.release_gate.validate_remote_gate", return_value=(True, "OK")):
            ret = cmd_run(
                config,
                mock_client,
                command=["pytest"],
                runner=mock_runner,
                tunnel_class=mock_tunnel_class,
                tunnel_enabled=True,
                password_fetcher=lambda cfg, r: "pass",
            )

    assert ret == 1
    mock_runner.assert_not_called()
    mock_tunnel.stop.assert_called_once()
    mock_client.shutdown.assert_called_once()


def test_cmd_run_returns_nonzero_when_shutdown_fails(capsys):
    config = make_clean_config()
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"
    # wait_for_status returns False during shutdown (CT101 fails to stop)
    mock_client.wait_for_status.return_value = False

    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(args=["pytest"], returncode=0)

    mock_tunnel = MagicMock()
    mock_tunnel.wait_ready.return_value = True
    mock_tunnel_class = MagicMock(return_value=mock_tunnel)

    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=True):
        with patch("scripts.dev.release_gate.validate_remote_gate", return_value=(True, "OK")):
            ret = cmd_run(
                config,
                mock_client,
                command=["pytest"],
                runner=mock_runner,
                tunnel_class=mock_tunnel_class,
                tunnel_enabled=True,
                password_fetcher=lambda cfg, r: "pass",
            )

    assert ret == 1
    err = capsys.readouterr().err
    assert "CRITICAL" in err
    assert "Container may remain running" in err


def test_cmd_run_shuts_down_when_startup_wait_times_out():
    config = make_clean_config()
    mock_client = MagicMock()
    mock_client.get_status.return_value = "stopped"
    mock_client.wait_for_status.side_effect = [False, True]

    ret = cmd_run(config, mock_client, command=["pytest"], runner=MagicMock(), tunnel_enabled=False)

    assert ret == 1
    mock_client.start.assert_called_once()
    mock_client.shutdown.assert_called_once()


def test_cmd_run_shuts_down_when_ssh_readiness_times_out():
    config = make_clean_config()
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"
    mock_client.wait_for_status.return_value = True

    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=False):
        ret = cmd_run(
            config, mock_client, command=["pytest"], runner=MagicMock(), tunnel_enabled=False
        )

    assert ret == 1
    mock_client.shutdown.assert_called_once()


def test_cmd_run_shuts_down_when_remote_validation_fails():
    config = make_clean_config()
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"
    mock_client.wait_for_status.return_value = True

    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=True):
        with patch(
            "scripts.dev.release_gate.validate_remote_gate",
            return_value=(False, "Non-PostgreSQL-17 runtime"),
        ):
            ret = cmd_run(
                config, mock_client, command=["pytest"], runner=MagicMock(), tunnel_enabled=False
            )

    assert ret == 1
    mock_client.shutdown.assert_called_once()


# --- 9. RESET NEVER LEAVES CT101 RUNNING WHEN INVOKED ALONE ---


def test_cmd_reset_starts_ct101_if_stopped_and_stops_it_in_finally():
    config = make_clean_config(allow_destructive=True)
    mock_client = MagicMock()
    mock_client.get_status.return_value = "stopped"
    mock_client.wait_for_status.return_value = True

    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(args=["ssh"], returncode=0)

    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=True):
        with patch("scripts.dev.release_gate.validate_remote_gate", return_value=(True, "OK")):
            ret = cmd_reset(config, mock_client, confirm=True, runner=mock_runner)

    assert ret == 0
    mock_client.start.assert_called_once()
    mock_runner.assert_called_once()
    cmd_args = mock_runner.call_args[0][0]
    assert cmd_args[0] == "ssh"
    assert "StrictHostKeyChecking=yes" in cmd_args
    assert (
        cmd_args[-1]
        == f"sudo -n -u lytrium-test env NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1 bash {CANONICAL_RESET_ENTRYPOINT}"
    )
    # CT101 must be gracefully shut down after reset!
    mock_client.shutdown.assert_called_once()


def test_cmd_reset_stops_ct101_even_if_already_running():
    config = make_clean_config(allow_destructive=True)
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"
    mock_client.wait_for_status.return_value = True

    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(args=["ssh"], returncode=0)

    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=True):
        with patch("scripts.dev.release_gate.validate_remote_gate", return_value=(True, "OK")):
            ret = cmd_reset(config, mock_client, confirm=True, runner=mock_runner)

    assert ret == 0
    mock_client.shutdown.assert_called_once()


def test_cmd_reset_fails_if_remote_validation_fails_and_shuts_down():
    config = make_clean_config(allow_destructive=True)
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"
    mock_client.wait_for_status.return_value = True

    mock_runner = MagicMock()
    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=True):
        with patch(
            "scripts.dev.release_gate.validate_remote_gate",
            return_value=(False, "Missing Vulcano Release Gate environment marker"),
        ):
            ret = cmd_reset(config, mock_client, confirm=True, runner=mock_runner)

    assert ret == 1
    mock_runner.assert_not_called()
    mock_client.shutdown.assert_called_once()


def test_cmd_reset_returns_nonzero_when_shutdown_fails(capsys):
    config = make_clean_config(allow_destructive=True)
    mock_client = MagicMock()
    mock_client.get_status.return_value = "running"
    mock_client.wait_for_status.return_value = False

    mock_runner = MagicMock()
    mock_runner.return_value = subprocess.CompletedProcess(args=["ssh"], returncode=0)

    with patch("scripts.dev.release_gate.wait_for_ssh_ready", return_value=True):
        with patch("scripts.dev.release_gate.validate_remote_gate", return_value=(True, "OK")):
            ret = cmd_reset(config, mock_client, confirm=True, runner=mock_runner)

    assert ret == 1
    err = capsys.readouterr().err
    assert "CRITICAL" in err
    assert "Container may remain running" in err


def test_destructive_reset_fails_closed_without_env_flag():
    config = make_clean_config(allow_destructive=False)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config, is_destructive=True, confirm=True)
    assert "NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1 is not set" in str(exc_info.value)


def test_destructive_reset_fails_closed_without_confirm_flag():
    config = make_clean_config(allow_destructive=True)
    with pytest.raises(SafetyViolationError) as exc_info:
        verify_safety_guards(config, is_destructive=True, confirm=False)
    assert "Missing explicit --confirm flag" in str(exc_info.value)


# --- 10. CLI PARSING RESTRICTIONS ---


def test_cli_has_no_script_override_option():
    with pytest.raises(SystemExit):
        main(["reset", "--confirm", "--script", "/untrusted/path.sh"])


def test_cli_has_no_keep_running_option():
    with pytest.raises(SystemExit):
        main(["run", "--keep-running", "--", "pytest"])


def test_main_cli_status(capsys):
    with patch.dict(
        "os.environ",
        {
            "NEXUM_PVE_API_URL": "https://192.168.0.200:8006",
            "NEXUM_PVE_TOKEN_ID": "root@pam!token",
            "NEXUM_PVE_TOKEN_SECRET": "sec123",
        },
    ):
        with patch.object(ProxmoxClient, "get_status", return_value="running"):
            ret = main(["status"])
            assert ret == 0
            assert "CT101" in capsys.readouterr().out


def test_main_cli_reset_fails_closed_without_confirm():
    with patch.dict("os.environ", {"NEXUM_ALLOW_DESTRUCTIVE_TEST_DB": "1"}):
        ret = main(["reset"])
        assert ret == 2


def test_main_cli_safety_violation_returns_code_2():
    with patch.dict("os.environ", {"NEXUM_LAB_VMID": "103"}):
        ret = main(["status"])
        assert ret == 2


# --- 11. BOOTSTRAP & RESET SCRIPT SAFETY AUDIT ---


def test_ct101_bootstrap_script_safety_invariants():
    from pathlib import Path

    script_path = Path(__file__).parent / "ct101_bootstrap.sh"
    assert script_path.exists(), "ct101_bootstrap.sh must exist"
    content = script_path.read_text(encoding="utf-8")

    assert 'EXPECTED_HOSTNAME="lab"' in content
    assert 'EXPECTED_IP="192.168.0.245"' in content
    assert 'GATE_DIR="/srv/lytrium/testing/nexum/release-gate"' in content
    assert 'MARKER_FILE="${GATE_DIR}/.vulcano-release-gate"' in content
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in content
    assert "nexum_release_gate_test" in content
    assert 'OS_USER="lytrium-test"' in content
    assert "sudo/wheel privileges" in content
    assert "idle_watchdog.sh" in content
    assert "shutdown -h now" in content
    assert "127.0.0.1" in content
    assert "postgresql-17" in content
    assert ".db_pass" in content
    assert 'chown root:root "${MARKER_FILE}"' in content
    assert 'chmod 600 "${DB_PASS_FILE}"' in content
    assert "rm -f /etc/sudoers.d" not in content
    # Debian package source only, no PGDG
    assert "pgdg" not in content
    # Canonical reset script staged root-owned mode 555
    assert 'RESET_SCRIPT="${GATE_DIR}/scripts/reset.sh"' in content
    assert 'chmod 555 "${RESET_SCRIPT}"' in content
    assert 'chown root:root "${RESET_SCRIPT}"' in content
    # Safe parameterized formatting
    assert "EXECUTE format(" in content
    # Strict umask 077 enforcement
    assert "umask 077" in content
    # Parent directory traversal permission for lytrium-test
    assert "chmod o+x /srv/lytrium /srv/lytrium/testing /srv/lytrium/testing/nexum" in content


def test_ct101_bootstrap_parent_dir_traversal_permissions():
    """Verify that bootstrap explicitly grants search (o+x) permissions to parent dirs for lytrium-test traversal."""
    from pathlib import Path

    script_path = Path(__file__).parent / "ct101_bootstrap.sh"
    assert script_path.exists(), "ct101_bootstrap.sh must exist"
    content = script_path.read_text(encoding="utf-8")

    expected_chmod = "chmod o+x /srv/lytrium /srv/lytrium/testing /srv/lytrium/testing/nexum"
    assert expected_chmod in content

    mkdir_pos = content.find('mkdir -p "${GATE_DIR}"/{scripts,incoming,working,logs}')
    chmod_pos = content.find(expected_chmod)
    pg_isready_pos = content.find('sudo -u "${OS_USER}" pg_isready')

    assert mkdir_pos != -1, "mkdir -p must be present"
    assert chmod_pos != -1, "parent directory chmod o+x must be present"
    assert pg_isready_pos != -1, "pg_isready verification as OS_USER must be present"
    assert mkdir_pos < chmod_pos < pg_isready_pos


def test_ct101_reset_script_safety_invariants():
    from pathlib import Path

    script_path = Path(__file__).parent / "ct101_reset.sh"
    assert script_path.exists(), "ct101_reset.sh must exist"
    content = script_path.read_text(encoding="utf-8")

    assert 'EXPECTED_HOSTNAME="lab"' in content
    assert 'MARKER_FILE="${GATE_DIR}/.vulcano-release-gate"' in content
    assert 'DB_NAME="nexum_release_gate_test"' in content
    assert 'DB_USER="nexum_release_gate_test"' in content
    assert "user has superuser privileges" in content
    assert "user has createrole privileges" in content
    assert "user has createdb privileges" in content
    assert "NEXUM_ALLOW_DESTRUCTIVE_TEST_DB=1" in content
    assert "127.0.0.1" in content


def test_embedded_reset_script_matches_ct101_reset_sh():
    """Verify that the embedded reset script inside ct101_bootstrap.sh is identical to ct101_reset.sh."""
    from pathlib import Path

    bootstrap_path = Path(__file__).parent / "ct101_bootstrap.sh"
    reset_path = Path(__file__).parent / "ct101_reset.sh"

    bootstrap_content = bootstrap_path.read_text(encoding="utf-8")
    reset_content = reset_path.read_text(encoding="utf-8")

    marker_start = "cat > \"${RESET_SCRIPT}\" <<'EOF'\n"
    marker_end = "\nEOF\nchmod 555"

    start_idx = bootstrap_content.find(marker_start)
    assert start_idx != -1, "Start of embedded reset script not found"
    start_idx += len(marker_start)

    end_idx = bootstrap_content.find(marker_end, start_idx)
    assert end_idx != -1, "End of embedded reset script not found"

    embedded_reset = bootstrap_content[start_idx:end_idx]
    assert embedded_reset.strip() == reset_content.strip()
