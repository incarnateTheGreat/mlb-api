"""
CSRF protection middleware using the double-submit cookie pattern.

How it works:
1. Frontend calls /auth/bootstrap to get a CSRF token cookie
2. For mutative requests, frontend sends the token in X-CSRF-Token header
3. This middleware compares cookie value vs header value
4. If they match, request is legitimate (attacker can't read cookies cross-origin)

This is similar to what we had in React Router's csrf.server.ts,
but now centralized in the API layer.
"""

import secrets
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class CSRFMiddleware(BaseHTTPMiddleware):
    """
    Middleware that validates CSRF tokens on mutative requests.
    
    Safe methods (GET, HEAD, OPTIONS) are allowed through.
    Certain paths can be exempted (webhooks, health checks, etc).
    """
    
    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
    EXEMPT_PATHS = {
        "/auth/bootstrap",
        "/health",
        "/",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
    
    async def dispatch(self, request: Request, call_next):
        # Skip safe methods (they don't modify state)
        if request.method in self.SAFE_METHODS:
            return await call_next(request)
        
        # Skip exempt paths
        path = request.url.path.rstrip("/")
        if path in self.EXEMPT_PATHS or path == "":
            return await call_next(request)
        
        # Get tokens from cookie and header
        cookie_token = request.cookies.get("csrf_token")
        header_token = request.headers.get("X-CSRF-Token")
        
        # Validate presence
        if not cookie_token:
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing CSRF cookie. Call /auth/bootstrap first."}
            )
        
        if not header_token:
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing X-CSRF-Token header"}
            )
        
        # Timing-safe comparison to prevent timing attacks
        if not secrets.compare_digest(cookie_token, header_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token mismatch"}
            )
        
        return await call_next(request)
