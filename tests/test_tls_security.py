"""
Guards against the TLS verification regression this module once shipped.

Every outbound MLB call previously ran with ``verify=False``, which turns off
certificate validation entirely and leaves traffic open to interception. The
root cause was a TLS-inspecting proxy whose CA is trusted by the OS but absent
from certifi's bundle; the fix was to verify against the OS trust store rather
than to stop verifying.

These tests are cheap and offline. They exist so that a future "just make it
work" change cannot quietly reintroduce the hole.
"""

import ast
import ssl
from pathlib import Path

import pytest

from app.services.mlb_client.base import BaseMLBClient, get_ssl_context

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def test_ssl_context_requires_certificates():
    """The shared context must actually validate the peer certificate."""
    ctx = get_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_ssl_context_is_cached():
    """Building the context reads the whole system store, so reuse it."""
    assert get_ssl_context() is get_ssl_context()


@pytest.mark.parametrize(
    "factory",
    ["_get_client_v1", "_get_client_live", "_get_client_graphql"],
)
@pytest.mark.asyncio
async def test_clients_are_built_with_verification(factory):
    """No client may be constructed with verification disabled."""
    client = BaseMLBClient()
    try:
        http_client = await getattr(client, factory)()
        # httpx stores the resolved context on the transport's connection pool.
        pool = http_client._transport._pool
        assert pool._ssl_context.verify_mode == ssl.CERT_REQUIRED
        assert pool._ssl_context.check_hostname is True
    finally:
        await client.close()


def test_no_verify_false_anywhere_in_app():
    """
    Belt-and-braces: scan the source for any ``verify=False`` keyword argument.

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
                    rel = path.relative_to(APP_DIR.parent)
                    offenders.append(f"{rel}:{kw.value.lineno}")

    assert not offenders, f"TLS verification disabled at: {offenders}"
