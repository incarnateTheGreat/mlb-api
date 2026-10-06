TASK_BREAKDOWN.md (Draft Content)
MLB AI Copilot Task Breakdown
Purpose
This breakdown turns the AI engineering plan into ticket-sized work items for the backend repo first, with clear dependencies and acceptance criteria.

Assumptions
Public data only (StatsAPI and openly licensed baseball docs).
Backend-first implementation in mlb-api.
Existing FastAPI, service, model, and cache patterns will be reused.
Milestone 1: Contract and Baseline Endpoint
T1. Define Copilot Request and Response Schemas
Owner: Backend
Estimate: 0.5 day
Depends on: None
Deliverables:
Typed request schema with query, mode, context, session_id.
Typed response schema with answer, citations, tools_used, model_info, timing, confidence, warnings.
Acceptance criteria:
Schemas validate valid payloads and reject malformed payloads.
Unit tests cover happy path and validation failures.
T2. Add Copilot Endpoint Skeleton
Owner: Backend
Estimate: 0.5 day
Depends on: T1
Deliverables:
New analysis route for copilot query handling.
Structured non-streaming JSON response.
Acceptance criteria:
Endpoint returns schema-compliant response.
Error envelope format is stable and documented.
T3. Baseline AI Service Integration
Owner: Backend
Estimate: 1 day
Depends on: T2
Deliverables:
Extend AI service to support copilot prompt execution.
Include model metadata and token accounting fields.
Acceptance criteria:
Basic queries return model answer and metadata.
Failures are mapped to standardized error codes.
T4. Request Tracing and Metrics Hook
Owner: Backend
Estimate: 0.5 day
Depends on: T2
Deliverables:
Request ID propagation and timing instrumentation.
Per-request logs for route choice and latency.
Acceptance criteria:
Each request logs request_id and total latency.
Log format is consistent and parseable.
Milestone 2: Tool Calling (StatsAPI)
T5. Tool Interface and Registry
Owner: Backend
Estimate: 0.5 day
Depends on: T1
Deliverables:
Generic tool interface with input validation and typed outputs.
Registry with deterministic invocation contract.
Acceptance criteria:
Tools can be invoked through one common execution function.
Invalid inputs return tool-level error envelope.
T6. Implement Tool: Game Summary
Owner: Backend
Estimate: 0.5 day
Depends on: T5
Deliverables:
Tool wrapper for game summary by gamePk.
Acceptance criteria:
Valid gamePk returns normalized schema output.
Unknown gamePk returns handled error, not exception.
T7. Implement Tool: Team Schedule
Owner: Backend
Estimate: 0.5 day
Depends on: T5
Deliverables:
Tool wrapper for team schedule with date range filters.
Acceptance criteria:
Response includes canonical date format and game identifiers.
Empty schedule windows are handled gracefully.
T8. Implement Tool: Player Stat Split
Owner: Backend
Estimate: 0.5 day
Depends on: T5
Deliverables:
Tool wrapper for player split stats by season and split type.
Acceptance criteria:
Split types are validated.
Missing player data returns clear tool error.
T9. Tool-Oriented Orchestration Path
Owner: Backend
Estimate: 1 day
Depends on: T6, T7, T8
Deliverables:
Route mode for tool-first queries.
Tool trace details included in final response.
Acceptance criteria:
Tool-first prompts call expected tool(s).
Final answer includes tool provenance summary.
Milestone 3: RAG Foundation
T10. Public Corpus Definition and Source Policy
Owner: Backend
Estimate: 0.5 day
Depends on: None
Deliverables:
Source whitelist and attribution requirements.
Metadata contract for title, URL, section, license note.
Acceptance criteria:
Every ingested source has required metadata.
Non-whitelisted sources are rejected.
T11. Ingestion Pipeline v1
Owner: Backend
Estimate: 1 day
Depends on: T10
Deliverables:
Loader, text normalization, chunking, metadata persistence.
Acceptance criteria:
Ingestion command processes sample corpus end-to-end.
Chunk stats are logged and reproducible.
T12. Embedding and Vector Storage
Owner: Backend
Estimate: 1 day
Depends on: T11
Deliverables:
Embedding generation and vector persistence.
Source-to-chunk mapping for citations.
Acceptance criteria:
Queryable vector entries are created for all chunks.
Chunk IDs resolve back to source metadata.
T13. Retrieval Service and Citation Builder
Owner: Backend
Estimate: 1 day
Depends on: T12
Deliverables:
Top-k retrieval with optional metadata filters.
Citation list generator with URL and snippet context.
Acceptance criteria:
Relevant chunks returned for benchmark prompts.
Citation objects are complete and schema-compliant.
T14. RAG-Only Orchestration Path
Owner: Backend
Estimate: 0.5 day
Depends on: T13
Deliverables:
Route mode for rag-first queries with grounded response formatting.
Acceptance criteria:
Rag-only prompts include citations.
Low-evidence prompts return uncertainty warning.
Milestone 4: Hybrid Orchestration and Reliability
T15. Hybrid Routing Policy
Owner: Backend
Estimate: 1 day
Depends on: T9, T14
Deliverables:
Auto classifier for tool-only, rag-only, hybrid.
Merge strategy for combining evidence from both channels.
Acceptance criteria:
Mixed prompts run both paths when appropriate.
Output clearly separates tool facts and document-backed context.
T16. Retry, Timeout, and Fallback Controls
Owner: Backend
Estimate: 1 day
Depends on: T15
Deliverables:
Retry policy with backoff for transient failures.
Stage timeout budgets and fallback model behavior.
Acceptance criteria:
Timeout simulation returns graceful degraded response.
No unhandled exceptions during induced provider failures.
T17. Caching and Rate Limiting
Owner: Backend
Estimate: 1 day
Depends on: T15
Deliverables:
Cache keys for repeated tool/retrieval requests.
Rate-limit policy by identity/session.
Acceptance criteria:
Repeated prompts show measurable latency reduction.
Rate-limited requests return consistent error envelope.
Milestone 5: Evaluation and Readiness
T18. Build 40-Prompt Eval Dataset
Owner: Backend
Estimate: 0.5 day
Depends on: T10
Deliverables:
15 tool-first, 15 rag-first, 10 hybrid prompts.
Expected answer rubric and grounding checks.
Acceptance criteria:
Dataset is versioned and reproducible.
Prompt categories are balanced and documented.
T19. Eval Runner and Score Report
Owner: Backend
Estimate: 1 day
Depends on: T18, T15
Deliverables:
Script to run batch evaluations and produce score summary.
Acceptance criteria:
Report includes correctness, groundedness, citation presence, latency, and cost.
Run completes without manual intervention.
T20. Quality Tuning and v1 Gate
Owner: Backend
Estimate: 1 day
Depends on: T19
Deliverables:
Prompt and retrieval tuning to meet pass thresholds.
Final release note for v1 readiness.
Acceptance criteria:
Grounded correctness >= 80%.
Citation presence >= 90% on rag and hybrid.
p95 latency within agreed budget.
Cross-Cutting Tickets
T21. Documentation Update
Owner: Backend
Estimate: 0.5 day
Depends on: T20
Deliverables:
Add setup and endpoint usage notes in README.md.
Acceptance criteria:
New contributors can run and test copilot locally.
T22. FE Handshake Spec
Owner: Backend + Frontend
Estimate: 0.5 day
Depends on: T1, T15
Deliverables:
Contract note for response rendering requirements and error states.
Acceptance criteria:
Frontend can integrate without contract ambiguity.
Execution Order Summary
Start: T1, T10
Then: T2, T3, T4, T5
Then parallel:
Tool path: T6, T7, T8, T9
RAG path: T11, T12, T13, T14
Merge and harden: T15, T16, T17
Evaluate and close: T18, T19, T20, T21, T22
Suggested Sprint Cadence
Week 1: T1 to T5
Week 2: T6 to T9 plus T11
Week 3: T12 to T15
Week 4: T16 to T22
