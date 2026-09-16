"""
Rate limiting service for Copilot requests.

Implements token-bucket style rate limiting per session/identity
to prevent abuse and manage resource utilization.
"""

import time
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict

from app.config import get_settings


@dataclass
class TokenBucket:
    """Token bucket for rate limiting."""
    
    capacity: int  # Max tokens
    tokens: float = field(default=0)  # Current tokens
    refill_rate: float = field(default=0)  # Tokens per second
    last_refill_time: float = field(default_factory=time.time)
    
    def try_acquire(self, count: int = 1) -> bool:
        """
        Try to acquire tokens from the bucket.
        
        Returns True if successful, False if insufficient tokens.
        """
        self._refill()
        if self.tokens >= count:
            self.tokens -= count
            return True
        return False
    
    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill_time
        self.tokens = min(
            self.capacity,
            self.tokens + (elapsed * self.refill_rate)
        )
        self.last_refill_time = now
    
    def get_tokens(self) -> int:
        """Get current token count."""
        self._refill()
        return int(self.tokens)


class RateLimiter:
    """
    Per-session/identity rate limiter.
    
    Tracks requests and enforces limits on a per-minute and per-hour basis.
    """
    
    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = settings.copilot_rate_limit_enabled
        self.per_minute_limit = settings.copilot_rate_limit_requests_per_minute
        self.per_hour_limit = settings.copilot_rate_limit_requests_per_hour
        self.rate_limit_by_session = settings.copilot_rate_limit_by_session
        
        # Token buckets per session_id/identity
        self.minute_buckets: dict[str, TokenBucket] = defaultdict(self._create_minute_bucket)
        self.hour_buckets: dict[str, TokenBucket] = defaultdict(self._create_hour_bucket)
        
        # Cleanup tracking (remove stale buckets periodically)
        self.last_cleanup = time.time()
        self.cleanup_interval_seconds = 300  # 5 minutes
    
    def _create_minute_bucket(self) -> TokenBucket:
        """Create a token bucket for per-minute rate limiting."""
        return TokenBucket(
            capacity=self.per_minute_limit,
            tokens=float(self.per_minute_limit),
            refill_rate=self.per_minute_limit / 60.0,  # Refill over 1 minute
        )
    
    def _create_hour_bucket(self) -> TokenBucket:
        """Create a token bucket for per-hour rate limiting."""
        return TokenBucket(
            capacity=self.per_hour_limit,
            tokens=float(self.per_hour_limit),
            refill_rate=self.per_hour_limit / 3600.0,  # Refill over 1 hour
        )
    
    def is_allowed(self, session_id: Optional[str] = None) -> tuple[bool, dict[str, str]]:
        """
        Check if a request is allowed under rate limits.
        
        Args:
            session_id: Optional session identifier. If not provided, uses 'default'.
        
        Returns:
            Tuple of (is_allowed: bool, metadata: dict with remaining counts)
        """
        if not self.enabled:
            return (True, {"reason": "rate limiting disabled"})
        
        # Cleanup stale buckets periodically
        now = time.time()
        if now - self.last_cleanup > self.cleanup_interval_seconds:
            self._cleanup_stale_buckets()
            self.last_cleanup = now
        
        # Use provided session_id or fall back to default
        identity = session_id or "default"
        
        # Check both minute and hour buckets
        minute_allowed = self.minute_buckets[identity].try_acquire()
        hour_allowed = self.hour_buckets[identity].try_acquire()
        
        is_allowed = minute_allowed and hour_allowed
        metadata = {
            "session_id": identity,
            "minute_remaining": str(self.minute_buckets[identity].get_tokens()),
            "hour_remaining": str(self.hour_buckets[identity].get_tokens()),
            "minute_allowed": "true" if minute_allowed else "false",
            "hour_allowed": "true" if hour_allowed else "false",
        }
        
        if not is_allowed:
            if not minute_allowed:
                metadata["limit_exceeded"] = "per_minute"
            if not hour_allowed:
                metadata["limit_exceeded"] = "per_hour"
        
        return (is_allowed, metadata)
    
    def _cleanup_stale_buckets(self):
        """Remove buckets older than 2 hours (beyond refill range)."""
        max_age_seconds = 7200  # 2 hours
        now = time.time()
        
        # Clean minute buckets
        stale_ids = [
            sid for sid, bucket in self.minute_buckets.items()
            if now - bucket.last_refill_time > max_age_seconds
        ]
        for sid in stale_ids:
            del self.minute_buckets[sid]
        
        # Clean hour buckets
        stale_ids = [
            sid for sid, bucket in self.hour_buckets.items()
            if now - bucket.last_refill_time > max_age_seconds
        ]
        for sid in stale_ids:
            del self.hour_buckets[sid]


# Global rate limiter instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create the global rate limiter."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter
