"""
Guards against the TLS verification regression this module once shipped.

Every outbound MLB call previously ran with ``verify=False``, which turns off
certificate validation entirely and leaves traffic open to interception. The
root cause was a TLS-inspecting proxy whose CA is trusted by the OS but absent
from certifi's bundle; the fix was to verify against the OS trust store rather
than to stop verifying.

A note on what is deliberately *not* asserted here. ``truststore`` performs
verification itself through the platform APIs, and to do that it briefly sets
the underlying OpenSSL context to ``CERT_NONE`` for the duration of each
handshake before restoring it (see ``truststore/_macos.py``). Sampling
``ctx.verify_mode`` therefore races with any in-flight connection and makes for
a flaky test. The behavioural test below is the real guarantee: a self-signed
certificate must be rejected.
"""

import ast
import datetime
import socket
import ssl
import threading
from pathlib import Path

import httpx
import pytest

from app.services.mlb_client.base import BaseMLBClient, get_ssl_context

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def test_ssl_context_is_cached():
    """Building the context reads the system store, so it should be reused."""
    assert get_ssl_context() is get_ssl_context()


@pytest.mark.parametrize(
    "factory",
    ["_get_client_v1", "_get_client_live", "_get_client_graphql"],
)
@pytest.mark.asyncio
async def test_clients_use_the_verified_shared_context(factory):
    """
    Every client must be wired to the OS-trust-store context.

    An identity check rather than a type check: ``verify=False`` still
    leaves httpx holding an ``SSLContext``, just one with no trust
    anchors, so merely asserting the type would pass on the broken code.
    """
    client = BaseMLBClient()
    try:
        http_client = await getattr(client, factory)()
        pool = http_client._transport._pool
        assert pool._ssl_context is get_ssl_context()
    finally:
        await client.close()


def test_no_verify_false_anywhere_in_app():
    """
    Scan for any ``verify=False`` keyword argument across the app.

    A direct ``httpx.AsyncClient(verify=False)`` somewhere new would not be
    caught by the tests above, since they only inspect the known factories.
    The check parses the AST rather than grepping text, so prose in comments
    and docstrings (including this module's own) is not mistaken for code.
    """
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "verify"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is False
                ):
                    offenders.append(
                        f"{path.relative_to(APP_DIR.parent)}:{kw.value.lineno}"
                    )

    assert not offenders, f"TLS verification disabled at: {offenders}"


def _self_signed_cert(tmp_path: Path) -> tuple[Path, Path]:
    """Write a throwaway self-signed cert/key pair for localhost."""
    pytest.importorskip("cryptography")

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(minutes=5))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    cert_path = tmp_path / "cert.pem"
    key_path = tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _serve_one_tls_request(cert_path: Path, key_path: Path):
    """
    Start a single-shot TLS server on loopback.

    Returns the port and the thread so the caller can tear it down. Entirely
    offline, so no proxy or outbound network is involved.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_path, key_path)

    def serve_once():
        try:
            conn, _ = listener.accept()
            with conn:
                # A rejecting client aborts mid-handshake; that is the
                # expected outcome, so swallow whatever it raises here.
                try:
                    with server_ctx.wrap_socket(conn, server_side=True) as tls:
                        tls.recv(1024)
                        tls.sendall(
                            b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n"
                        )
                except OSError:
                    pass
        except OSError:
            pass

    thread = threading.Thread(target=serve_once, daemon=True)
    thread.start()
    return listener, port, thread


def test_a_self_signed_certificate_is_rejected(tmp_path):
    """
    The guarantee that actually matters, proved end to end.

    A local TLS server presents a certificate signed by nobody. If the
    client still completes the request, verification is not happening and
    the man-in-the-middle hole is back.
    """
    cert_path, key_path = _self_signed_cert(tmp_path)
    listener, port, thread = _serve_one_tls_request(cert_path, key_path)

    try:
        with httpx.Client(verify=get_ssl_context(), timeout=10.0) as client:
            with pytest.raises(httpx.ConnectError) as excinfo:
                client.get(f"https://localhost:{port}/")

        assert "certificate" in str(excinfo.value).lower()
    finally:
        listener.close()
        thread.join(timeout=5)


def test_the_rejection_test_can_actually_fail(tmp_path):
    """
    Confirms the test above is not passing for the wrong reason.

    Proving a bad certificate is refused only means something if the same
    server is reachable when verification is off. Without this, a typo in
    the URL would produce a passing test that checks nothing.
    """
    cert_path, key_path = _self_signed_cert(tmp_path)
    listener, port, thread = _serve_one_tls_request(cert_path, key_path)

    insecure = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE

    try:
        with httpx.Client(verify=insecure, timeout=10.0) as client:
            assert client.get(f"https://localhost:{port}/").status_code == 200
    finally:
        listener.close()
        thread.join(timeout=5)
