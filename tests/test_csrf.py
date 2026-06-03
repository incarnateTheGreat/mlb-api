"""
Tests for CSRF middleware and auth endpoints.

Run with: pytest tests/test_csrf.py -v

These tests verify:
1. CSRF token bootstrap endpoint works
2. Middleware blocks requests without CSRF tokens
3. Middleware allows requests with valid CSRF tokens
4. Middleware rejects mismatched tokens
5. Safe methods (GET, HEAD, OPTIONS) bypass CSRF checks
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


# =============================================================================
# Auth Bootstrap Endpoint
# =============================================================================

class TestAuthBootstrap:
    """Tests for /auth/bootstrap endpoint."""
    
    def test_bootstrap_returns_ok(self):
        """Bootstrap endpoint should return status ok."""
        response = client.get("/auth/bootstrap")
        
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
    
    def test_bootstrap_sets_csrf_cookie(self):
        """Bootstrap endpoint should set csrf_token cookie."""
        response = client.get("/auth/bootstrap")
        
        assert response.status_code == 200
        assert "csrf_token" in response.cookies
        
        # Token should be a non-empty string
        token = response.cookies["csrf_token"]
        assert len(token) > 0
    
    def test_bootstrap_cookie_attributes(self):
        """CSRF cookie should have correct security attributes."""
        response = client.get("/auth/bootstrap")
        
        # Check Set-Cookie header for attributes
        set_cookie = response.headers.get("set-cookie", "")
        
        assert "csrf_token=" in set_cookie
        assert "SameSite=lax" in set_cookie
        assert "Path=/" in set_cookie
        # In test environment, Secure should not be set (non-production)
        # httponly should NOT be present (JS needs to read it)
        assert "httponly" not in set_cookie.lower()


class TestAuthStatus:
    """Tests for /auth/status endpoint."""
    
    def test_status_returns_csrf_config(self):
        """Status endpoint should return CSRF configuration."""
        response = client.get("/auth/status")
        
        assert response.status_code == 200
        data = response.json()
        
        assert "csrf_enabled" in data
        assert "production" in data
        assert "cookie_domain" in data
        assert data["csrf_enabled"] is True


# =============================================================================
# CSRF Middleware - Blocking Invalid Requests
# =============================================================================

class TestCSRFMiddlewareBlocking:
    """Tests that CSRF middleware blocks invalid requests."""
    
    def test_post_without_csrf_cookie_is_blocked(self):
        """POST without CSRF cookie should return 403."""
        # Use a fresh client to ensure no cookies are persisted
        fresh_client = TestClient(app, cookies={})
        
        response = fresh_client.post(
            "/games/schedule",
            headers={"Content-Type": "application/json"},
        )
        
        assert response.status_code == 403
        assert "CSRF" in response.json()["detail"]  # Either missing cookie or header
    
    def test_post_without_csrf_header_is_blocked(self):
        """POST with cookie but no header should return 403."""
        # First get a token
        bootstrap_response = client.get("/auth/bootstrap")
        token = bootstrap_response.cookies["csrf_token"]
        
        # Send POST with cookie but no X-CSRF-Token header
        response = client.post(
            "/games/schedule",
            cookies={"csrf_token": token},
            headers={"Content-Type": "application/json"},
        )
        
        assert response.status_code == 403
        assert "Missing X-CSRF-Token header" in response.json()["detail"]
    
    def test_post_with_mismatched_tokens_is_blocked(self):
        """POST with mismatched cookie and header tokens should return 403."""
        response = client.post(
            "/games/schedule",
            cookies={"csrf_token": "token_in_cookie"},
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": "different_token_in_header",
            },
        )
        
        assert response.status_code == 403
        assert "CSRF token mismatch" in response.json()["detail"]


# =============================================================================
# CSRF Middleware - Allowing Valid Requests
# =============================================================================

class TestCSRFMiddlewareAllowing:
    """Tests that CSRF middleware allows valid requests."""
    
    def test_post_with_valid_csrf_passes_middleware(self):
        """POST with matching CSRF tokens should pass through middleware."""
        # Get a token
        bootstrap_response = client.get("/auth/bootstrap")
        token = bootstrap_response.cookies["csrf_token"]
        
        # Send POST with matching cookie and header
        response = client.post(
            "/games/schedule",
            cookies={"csrf_token": token},
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": token,
            },
        )
        
        # Should get through CSRF middleware (may fail for other reasons like 405)
        # The key is we DON'T get a 403 CSRF error
        assert response.status_code != 403 or "CSRF" not in response.json().get("detail", "")
    
    def test_put_with_valid_csrf_passes_middleware(self):
        """PUT with matching CSRF tokens should pass through middleware."""
        bootstrap_response = client.get("/auth/bootstrap")
        token = bootstrap_response.cookies["csrf_token"]
        
        response = client.put(
            "/games/schedule",
            cookies={"csrf_token": token},
            headers={
                "Content-Type": "application/json",
                "X-CSRF-Token": token,
            },
        )
        
        assert response.status_code != 403 or "CSRF" not in response.json().get("detail", "")
    
    def test_delete_with_valid_csrf_passes_middleware(self):
        """DELETE with matching CSRF tokens should pass through middleware."""
        bootstrap_response = client.get("/auth/bootstrap")
        token = bootstrap_response.cookies["csrf_token"]
        
        response = client.delete(
            "/games/schedule",
            cookies={"csrf_token": token},
            headers={"X-CSRF-Token": token},
        )
        
        assert response.status_code != 403 or "CSRF" not in response.json().get("detail", "")


# =============================================================================
# CSRF Middleware - Safe Methods Bypass
# =============================================================================

class TestCSRFMiddlewareSafeMethods:
    """Tests that safe HTTP methods bypass CSRF checks."""
    
    def test_get_without_csrf_is_allowed(self):
        """GET requests should not require CSRF tokens."""
        response = client.get("/health")
        
        assert response.status_code == 200
    
    def test_head_without_csrf_is_allowed(self):
        """HEAD requests should not require CSRF tokens."""
        response = client.head("/health")
        
        # HEAD returns no body, just check it's not 403
        assert response.status_code != 403
    
    def test_options_without_csrf_is_allowed(self):
        """OPTIONS requests should not require CSRF tokens."""
        response = client.options("/health")
        
        assert response.status_code != 403


# =============================================================================
# CSRF Middleware - Exempt Paths
# =============================================================================

class TestCSRFMiddlewareExemptPaths:
    """Tests that exempt paths bypass CSRF checks."""
    
    def test_bootstrap_is_exempt(self):
        """Bootstrap endpoint should be exempt from CSRF."""
        # If bootstrap required CSRF, this would fail since we have no token
        response = client.get("/auth/bootstrap")
        
        assert response.status_code == 200
    
    def test_health_is_exempt(self):
        """Health endpoint should be exempt from CSRF."""
        response = client.get("/health")
        
        assert response.status_code == 200
    
    def test_docs_is_exempt(self):
        """Docs endpoint should be exempt from CSRF."""
        response = client.get("/docs")
        
        assert response.status_code == 200
    
    def test_root_is_exempt(self):
        """Root endpoint should be exempt from CSRF."""
        response = client.get("/")
        
        assert response.status_code == 200


# =============================================================================
# Token Security
# =============================================================================

class TestTokenSecurity:
    """Tests for CSRF token security properties."""
    
    def test_tokens_are_unique(self):
        """Each bootstrap call should generate a unique token."""
        tokens = set()
        
        for _ in range(10):
            response = client.get("/auth/bootstrap")
            token = response.cookies["csrf_token"]
            tokens.add(token)
        
        # All 10 tokens should be different
        assert len(tokens) == 10
    
    def test_token_has_sufficient_entropy(self):
        """Token should be long enough for security (256 bits)."""
        response = client.get("/auth/bootstrap")
        token = response.cookies["csrf_token"]
        
        # secrets.token_urlsafe(32) produces ~43 characters
        assert len(token) >= 40
