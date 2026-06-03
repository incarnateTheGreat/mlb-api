"""
Authentication router for security bootstrapping.

This module provides the CSRF token bootstrap endpoint that the frontend
calls on initialization. The token is set as a cookie and the frontend
reads it to include in subsequent mutative requests.

This replaces the React Router server-side CSRF logic, centralizing
security in the API layer.
"""

import secrets
from fastapi import APIRouter, Response

from app.config import get_cookie_domain, is_production


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/bootstrap")
async def bootstrap_csrf(response: Response) -> dict:
    """
    Generate a CSRF token and set it as a cookie.
    
    The frontend should call this endpoint:
    - On initial app load
    - When the token expires
    - After receiving a 403 CSRF error
    
    The token is stored in a cookie that:
    - Is readable by JavaScript (httponly=False) so it can be sent in headers
    - Uses SameSite=Lax to prevent CSRF while allowing same-site requests
    - Is Secure in production (HTTPS only)
    
    Returns:
        {"status": "ok"} - The token is in the Set-Cookie header
    """
    # Generate a cryptographically secure random token
    # 32 bytes = 256 bits of entropy, URL-safe base64 encoded
    token = secrets.token_urlsafe(32)
    
    response.set_cookie(
        key="csrf_token",
        value=token,
        max_age=60 * 60 * 24,  # 24 hours
        httponly=False,  # Frontend JS needs to read this
        secure=is_production(),
        samesite="lax",
        domain=get_cookie_domain(),
        path="/",
    )
    
    return {"status": "ok"}


@router.get("/status")
async def auth_status() -> dict:
    """
    Check authentication/security status.
    
    Useful for debugging and health checks.
    """
    return {
        "csrf_enabled": True,
        "production": is_production(),
        "cookie_domain": get_cookie_domain(),
    }
