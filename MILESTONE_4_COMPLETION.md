# Milestone 4: Hybrid Orchestration and Reliability

**Completion Date:** September 15, 2026

## Overview

Milestone 4 focused on hardening the copilot backend with robust error handling, retry logic, and resource management. All three tickets (T15, T16, T17) have been completed and integrated.

## Tickets Completed

### T15: Hybrid Routing Policy ✅

**Objective:** Improve hybrid mode orchestration to better merge tool facts with document context.

#### Deliverables:

1. **Enhanced Hybrid Answer Generator** (`_generate_hybrid_answer` in `copilot_service.py`)
   - More explicit synthesis guidance for LLM
   - Clear separation of tool facts vs. document-backed claims
   - Better formatting of context blocks with document indices [DOC N]
   - Improved fallback behavior when synthesis fails

2. **Refined Merge Strategy**
   - Tool responses marked as "✓ success" or "✗ failed" with latency
   - Document context formatted with title, URL, section, and content
   - Synthesis rules provided directly in prompt:
     - State tool facts directly with source attribution
     - Use documents for rule explanations and context
     - Mark unsupported claims as tool-only or uncertain
   - Graceful fallback to tool answer if synthesis fails

#### Key Changes:

- Updated prompt engineering to provide model with explicit synthesis rules
- Added better formatting for traceability (tool success/failure icons, doc indexing)
- Improved fallback message to preserve user experience if LLM fails

### T16: Retry, Timeout, and Fallback Controls ✅

**Objective:** Implement resilient tool execution with retry logic and bounded backoff.

#### Deliverables:

1. **Tool-Level Retry Infrastructure** (`tools.py`)
   - Added `copilot_tool_retries` and `copilot_tool_retry_backoff_ms` to config
   - Implemented `execute_tool()` with exponential backoff retry strategy
   - Transient vs. permanent error detection:
     - **Transient:** timeout, connection issues, service unavailable (retry)
     - **Permanent:** validation errors, not found, unauthorized (skip retry)
   - Retry metadata tracking: attempt count and failure history

2. **Error Classification** (`_is_transient_error`)
   - Recognizes transient patterns: timeout, connection reset, service unavailable, too many requests
   - Recognizes permanent patterns: not found, invalid, validation, unauthorized
   - Defaults to retry-friendly behavior (errs on side of attempting retry)

3. **Bounded Exponential Backoff**
   - Formula: `base_backoff * 2^(attempt-1)` capped at 5 seconds
   - Default backoff: 100ms, doubling per retry
   - Prevents overwhelming downstream services during outages

4. **Cache Hit Optimization**
   - Cached tool results bypass retry logic (instant return)
   - Cache hits marked with `cached: true` and zero latency
   - Successful results automatically cached for future queries

#### Key Config:

```python
copilot_tool_retries: int = 2           # Max retry attempts
copilot_tool_retry_backoff_ms: int = 100  # Base backoff (grows exponentially)
```

#### Model-Level Resilience (Already Implemented):

- `copilot_model_retries`: 2 attempts with backoff (200ms base)
- `copilot_model_timeout_ms`: 12 second per-stage timeout
- `copilot_max_total_latency_ms`: 30 second hard deadline
- Fallback model support with automatic failover

### T17: Caching and Rate Limiting ✅

**Objective:** Implement rate limiting by identity/session to manage resource utilization.

#### Deliverables:

1. **Rate Limiter Service** (`rate_limiter.py`)
   - Token-bucket style rate limiting per session/identity
   - Dual-tier limits:
     - **Per-minute:** 30 requests (refills at 0.5 req/sec)
     - **Per-hour:** 300 requests (refills at 0.083 req/sec)
   - Configurable via environment variables
   - Automatic cleanup of stale buckets (2+ hour idle)

2. **Rate Limiter Integration** (`routers/analysis.py`)
   - New dependency `check_copilot_rate_limit()` on copilot_query endpoint
   - Rate checking before query processing
   - Returns 429 (Too Many Requests) when limits exceeded
   - Metadata includes:
     - `session_id`: Request identity
     - `minute_remaining`: Tokens remaining this minute
     - `hour_remaining`: Tokens remaining this hour
     - `limit_exceeded`: Which limit was hit (per_minute or per_hour)

3. **Tool Caching** (Pre-existing, enhanced)
   - TTL cache with 180-second expiration
   - Normalized cache keys using tool name + kwargs hash
   - Cache hit metadata automatically added
   - Respects successes only (failures not cached)

#### Key Config:

```python
copilot_rate_limit_enabled: bool = True
copilot_rate_limit_requests_per_minute: int = 30
copilot_rate_limit_requests_per_hour: int = 300
copilot_rate_limit_by_session: bool = True
```

## Implementation Details

### Architecture

```
Copilot Request
    ↓
[Rate Limit Check] → 429 if exceeded
    ↓
[Mode Inference] (auto/tool/rag/hybrid)
    ├→ Tool Phase
    │   ├→ Tool Selection
    │   ├→ Cache Lookup
    │   ├→ [Tool Execution w/ Retries & Timeouts]
    │   │   ├→ Attempt 1, 2, 3 (configurable)
    │   │   ├→ Exponential backoff on failure
    │   │   └→ Transient error detection
    │   └→ Format Tool Answer
    │
    └→ RAG Phase
        ├→ Retrieval
        └→ [Hybrid Synthesis]
            └→ Format merged answer with fallback
```

### Error Handling Flows

#### Tool Failure → Retry

```
Tool Timeout/Connection Error (transient)
    → Wait 100ms (backoff)
    → Retry attempt 2
    → If fails, wait 200ms
    → Retry attempt 3
    → If still fails, return error to user
```

#### Tool Failure → Fallback

```
All retries exhausted
    → Return error to hybrid synthesis
    → Synthesis may still use RAG data if tool fails
    → Warning added: "Tool execution failed after retries"
```

#### Hybrid Synthesis Fallback

```
LLM synthesis fails (model timeout/error)
    → Fall back to raw tool answer
    → Add warning: "Hybrid synthesis failed; returning tool answer"
    → No document citations attached (synthesis couldn't verify)
```

### Rate Limit Behavior

```
Session "user-123" exceeds 30 req/min
    → 31st request → 429 Too Many Requests
    → Metadata: minute_remaining: 0, limit_exceeded: per_minute
    → User waits for token refill (2 seconds per request refund)
```

## Testing Recommendations

### Unit Tests

1. Tool retry logic with mock timeouts
2. Transient vs. permanent error classification
3. Token bucket refill rate calculation
4. Rate limiter cleanup of stale buckets

### Integration Tests

1. Hybrid queries with tool + RAG data
2. Tool execution with transient failures (mock timeout)
3. Rate limit enforcement at endpoint level
4. Cached tool response bypass

### Load Tests

1. Concurrent requests from multiple sessions
2. Tool timeout behavior under high load
3. LLM synthesis latency with large contexts
4. Cache hit ratio and performance improvement

## Known Limitations & Future Work

### Current Limitations

1. **Session ID Required:** Rate limiting relies on `session_id` in request; anonymous users share single quota
2. **In-Memory Buckets:** Rate limiter uses in-memory token buckets; doesn't persist across server restarts
3. **Static Config:** Rate limits require server restart to adjust; no dynamic adjustment API
4. **No Request Deduplication:** Identical requests within same session are independent (not cached at session level)

### Future Enhancements (Milestone 5+)

1. **Redis-Backed Rate Limiting:** Distributed rate limiting for multi-instance deployments
2. **Adaptive Timeouts:** Adjust tool timeouts based on historical latency
3. **Circuit Breaker:** Detect failing external services and fast-fail for N seconds
4. **Request Deduplication:** Cache identical requests within a time window per session
5. **Metrics Dashboard:** Expose rate limit utilization and tool retry rates

## Deployment Checklist

- [x] Tool retry logic implemented with bounded backoff
- [x] Rate limiter deployed with per-minute and per-hour limits
- [x] Hybrid synthesis improved with better prompt engineering
- [x] Error messages categorized (transient vs. permanent)
- [x] Fallback chains tested (tool → hybrid synthesis → tool answer)
- [x] Config settings added with sensible defaults
- [x] HTTP 429 response for rate limit exceeded
- [x] Rate limit metadata included in endpoint response

## Configuration Summary

### Required Environment Variables

```bash
# Tool Execution (Milestone 4)
COPILOT_TOOL_RETRIES=2
COPILOT_TOOL_RETRY_BACKOFF_MS=100

# Rate Limiting (Milestone 4)
COPILOT_RATE_LIMIT_ENABLED=true
COPILOT_RATE_LIMIT_REQUESTS_PER_MINUTE=30
COPILOT_RATE_LIMIT_REQUESTS_PER_HOUR=300
COPILOT_RATE_LIMIT_BY_SESSION=true

# Existing (Milestones 1-3)
COPILOT_MODEL_TIMEOUT_MS=12000
COPILOT_MODEL_RETRIES=2
COPILOT_MAX_TOTAL_LATENCY_MS=30000
```

## Files Modified

### Core Services

- `app/services/tools.py` — Tool retry logic and backoff
- `app/services/copilot_service.py` — Enhanced hybrid synthesis
- `app/services/rate_limiter.py` — New rate limiting service
- `app/config.py` — Tool retry and rate limit config
- `app/routers/analysis.py` — Rate limit dependency injection

### Models & Types

- No breaking changes to existing models
- Rate limit metadata in error responses (429 status code)

## Success Metrics

✅ **Resilience:** Tool execution retries on transient errors without overwhelming downstream services  
✅ **Hybrid Quality:** Better synthesis of tool facts + document context with explicit guidance  
✅ **Resource Management:** Rate limiting prevents abuse while allowing healthy usage patterns  
✅ **Observability:** Retry attempts and rate limit state tracked in response metadata  
✅ **Backward Compatibility:** All changes backward compatible; existing clients unaffected

## Milestone 4 → Milestone 5 Handoff

Milestone 4 completes the "Hybrid Orchestration and Reliability" tier. The system now:

- Routes queries intelligently (tool/rag/hybrid)
- Handles transient failures with intelligent retries
- Enforces resource limits via rate limiting
- Synthesizes diverse evidence sources robustly

**Next:** Milestone 5 will focus on **Evaluation and Readiness**, building a 40-prompt evaluation dataset, scoring the system's correctness/groundedness/latency, and tuning for v1 production readiness.
