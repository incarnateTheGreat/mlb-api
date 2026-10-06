AI Engineering Plan: MLB Public Data Copilot

1. Goal
   Build a production-style AI capability in the MLB API backend that can:

Answer MLB questions using public knowledge with citations.
Fetch live/historical baseball data through deterministic tools.
Combine tool outputs and retrieved documents in one grounded response.
Return trace metadata (latency, token usage, model, tool calls, citation sources).
This project is designed to build transferable AI engineering skills without requiring model training or company data.

2. Non-Goals
   Training or fine-tuning foundation models.
   Using internal/company/private data.
   Building autonomous write-actions against external systems.
   Creating a frontend-first architecture that bypasses backend AI controls.
3. Public Data Policy
   Allowed sources (v1):

MLB StatsAPI for structured live and historical baseball data.
Public baseball documentation pages (rules, glossary, historical summaries) with attribution.
Public datasets with clear usage terms (optional expansion after v1).
Policy requirements:

Every grounded answer must include source references.
Responses must distinguish facts from inference.
If evidence is weak or conflicting, the assistant must say it is uncertain.
No proprietary/team-private information is accepted in prompt context. 4) Existing Architecture Reuse Map
Primary reuse targets:

app/services/ai_service.py: base LLM invocation and metadata handling.
app/services/mlb_client: source of deterministic baseball tool actions.
app/services/cache_service.py: persistent caching patterns.
app/services/memory_cache.py: short-lived in-memory caching.
app/routers/analysis.py: likely insertion point for copilot endpoint(s).
app/models/analysis.py: response model extensions for citations/tool traces.
app/config.py: environment settings for AI/rag/runtime controls.
tests: testing patterns and CI-compatible verification layout.
pyproject.toml: lint/type/test conventions to maintain. 5) API Contract (v1)
Endpoint intent:

Single copilot query endpoint.
Supports three query classes: tool-only, rag-only, hybrid.
Returns structured fields for answer, citations, and tool provenance.
Request shape (conceptual):

query: user question text.
context: optional game/team/player identifiers.
mode: auto | tool | rag | hybrid.
session_id: optional conversation grouping.
constraints: optional response format and max cost/latency hints.
Response shape (conceptual):

answer: final natural-language response.
citations: list of sources with title, URL, and snippet metadata.
tools_used: list of tool invocations with input summary and success/failure state.
model_info: provider/model and token usage.
timing: total latency and stage-level timings.
confidence: simple signal (high/medium/low) based on evidence sufficiency.
warnings: fallback notes, uncertainty flags, or partial-data signals. 6) Tool Catalog v1 (Deterministic)
Initial tools:

get_game_summary(gamePk)
get_team_schedule(teamId, startDate, endDate)
get_player_stat_split(playerId, season, splitType)
Tool requirements:

Strict input validation and clear error envelopes.
Idempotent, read-only behavior.
Bounded execution timeouts.
Cache key normalization for repeated requests.
Standardized output schemas for model consumption. 7) RAG Design v1
Corpus:

Public baseball docs and reference pages with stable URLs.
Optional curated markdown snapshots for deterministic ingestion.
Ingestion:

Normalize text and metadata (title, URL, section, updated_at).
Chunk by semantic boundaries with overlap.
Generate embeddings and store vectors plus metadata.
Maintain source_id for traceability.
Retrieval:

Top-k semantic retrieval with lightweight metadata filtering.
Optional rerank pass if quality is weak.
Context budgeting to respect latency and token costs.
Citation mapping from chunk to final answer statements.
Grounding rules:

Claims requiring evidence must map to retrieved chunks or tool outputs.
If no strong support exists, answer with uncertainty and recommended follow-up query. 8) Orchestration Policy (Auto Mode)
Routing logic:

Data-live questions route to tools first.
Concept/history questions route to RAG first.
Mixed questions execute both and synthesize.
Synthesis requirements:

Final answer separates tool facts and doc-backed context.
Citations include both document sources and tool provenance entries.
Conflicts between sources are explicitly surfaced.
Fallback behavior:

Tool failure: degrade to available evidence and disclose gap.
Retrieval failure: answer from tool data only if valid, otherwise uncertain response.
Model timeout/rate-limit: retry with bounded backoff, then fallback model tier. 9) Reliability, Security, and Cost Controls
Reliability:

Request timeout budgets with stage sub-budgets.
Exponential backoff retries for transient model/provider failures.
Graceful partial response when one subsystem fails.
Security:

API keys only in backend runtime env.
Input length limits and basic prompt-injection hardening.
Log redaction for sensitive runtime values.
Cost control:

Per-request token and cost accounting.
Rate limits by client identity/session.
Cache repeated tool and retrieval-heavy queries.
Adjustable max context and max tokens by environment. 10) Observability
Minimum telemetry:

request_id across all stages.
route decision: tool, rag, hybrid.
tool call count and per-tool latency.
retrieval stats: candidates, selected chunks, source diversity.
model stats: prompt tokens, completion tokens, total latency.
result quality tags: grounded, partial, uncertain. 11) Evaluation Plan (Public Data Only)
Eval dataset (v1, 40 prompts):

15 tool-first prompts (live/historical stat lookups).
15 rag-first prompts (rules/concepts/history).
10 hybrid prompts (stat-backed explanation with references).
Scoring axes:

Correctness
Groundedness
Citation quality
Latency
Cost per request
Robustness under partial failures
Pass gates for v1 readiness:

Grounded correctness >= 80% overall.
Citation presence >= 90% on rag/hybrid prompts.
p95 latency within agreed budget.
Zero uncaught exceptions in eval runs. 12) Delivery Milestones (4 Weeks)
Week 1: Contract + Baseline Endpoint

Define request/response schemas and validation.
Implement baseline copilot route returning structured metadata.
Add telemetry hooks and basic error envelopes.
Done criteria: endpoint stable in local testing with deterministic shape.
Week 2: Tool Layer Integration

Implement and register 3 StatsAPI tools.
Add tool selection/execution path in orchestration.
Add cache for tool results.
Done criteria: tool-only prompts return correct data and trace output.
Week 3: RAG Integration

Implement ingestion pipeline and vector retrieval.
Add citation emission in responses.
Add rag-only and hybrid orchestration branches.
Done criteria: rag/hybrid prompts include usable citations and uncertainty handling.
Week 4: Hardening + Evals

Add retries, timeout budgets, and fallback tiering.
Run 40-prompt eval suite and collect metrics.
Tune chunking/retrieval and prompt templates for threshold pass.
Done criteria: pass gates met and demo flow is stable. 13) Testing and Verification
Automated:

Unit tests for tool adapters and schema validators.
Service tests for orchestration branch selection.
Integration tests for endpoint responses and error envelopes.
Eval runner for fixed prompt set and score reporting.
Manual checks:

Live prompt smoke tests for each route class.
Citation spot checks against source pages.
Failure injection tests (tool timeout, model timeout, empty retrieval). 14) Risks and Mitigations
Risk: source licensing ambiguity
Mitigation: whitelist only clearly permissible sources and require attribution metadata at ingest time.

Risk: hallucinated synthesis in hybrid mode
Mitigation: enforce evidence-linked claim policy and uncertainty fallback.

Risk: latency spikes from hybrid flow
Mitigation: parallelize tool/retrieval where safe, add strict stage budgets and caching.

Risk: API drift in public endpoints
Mitigation: schema guards around tool outputs and alerting for parsing failures.

15. FE Integration Handshake (for later)
    Backend guarantees:

Stable response schema with answer, citations, tools_used, model_info, timing, confidence.
Error envelope with machine-readable code and user-safe message.
Streaming compatibility can be introduced after non-streaming contract stabilization.
Frontend expectations:

Render citations and tool traces clearly.
Show uncertainty/warnings when evidence is weak.
Surface latency and partial-result states. 16) Definition of Done (Project)
Public-data-only policy is enforced and documented.
Tool-only, rag-only, and hybrid questions all function in production-style flow.
Observability and cost metrics are visible per request.
Eval thresholds are met with reproducible results.
Architecture is reusable across non-MLB domains with minimal code changes.
