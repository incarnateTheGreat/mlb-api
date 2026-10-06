# Milestone 5: Evaluation and Readiness — COMPLETE

**Completion Date:** September 16, 2026  
**Status:** Framework Complete | Testing Deferred (Awaiting API Access)

---

## Executive Summary

Milestone 5 delivered a **complete evaluation framework** for assessing Copilot v1 production readiness. The system includes a comprehensive 40-prompt dataset, automated scoring pipeline, and objective quality gates. The framework is ready to execute immediately upon API access.

---

## Deliverables

### T18: 40-Prompt Evaluation Dataset ✅ COMPLETE

**File:** `eval/evaluation_dataset.json`

**Structure:**

- **15 Tool-Only Queries** (direct MLB data access)
  - Game scores, player stats (hitting/pitching), team schedules
  - Expected source: StatsAPI via `get_game_summary`, `get_player_stat_split`, `get_team_schedule`
- **15 RAG-Only Queries** (knowledge base lookups)
  - Rules (infield fly, DH, strikes, double play)
  - History (World Series winners, famous records)
  - Analytics (sabermetrics, WAR, OPS, league structure)
  - Expected source: Document corpus (rules*\*, history*_, stats*sabermetrics, structure*_)

- **10 Hybrid Queries** (tool + document synthesis)
  - Combine live data with historical context
  - Example: "Trout's 2024 HR and historical rank?"
  - Expected source: Both tool AND document citations

**Dataset Characteristics:**

- Each prompt includes: query, expected_answer, grounding_rules, acceptance_criteria
- Grounding rules specify which sources MUST be cited
- Acceptance criteria define what "correct" looks like
- Categories balanced for representative coverage

**Sample Entry:**

```json
{
  "id": "hybrid_001",
  "query": "What's Mike Trout's 2024 home run total and how does that rank historically?",
  "expected_answer": "Provides Trout's 2024 HR count and contextualizes against historical records",
  "grounding_rules": [
    "Must cite tool:get_player_stat_split for current stats",
    "Must cite document source (history_records_achievements) for context",
    "Should clearly separate fact (tool) from context (doc)"
  ],
  "acceptance_criteria": {
    "correctness": "high",
    "has_citations": true,
    "min_citation_count": 2,
    "citation_mix": ["tool", "document"]
  }
}
```

---

### T19: Evaluation Runner Framework ✅ COMPLETE

**File:** `eval/eval_runner.py`

**Core Components:**

1. **Async Prompt Executor**
   - Sends each prompt to `CopilotService.query()`
   - Captures: answer, citations, tools_used, latency, warnings
   - Handles execution errors gracefully

2. **Heuristic Scorer (1-5 Scale)**

   ```
   Correctness:
     • 1 = Empty/completely wrong
     • 3 = Contains some expected information
     • 5 = Direct, detailed answer to expected outcome

   Groundedness:
     • 1 = No citations or wrong source type
     • 3 = Citations present but potentially incomplete
     • 5 = Citations perfectly match category (tool→tool, rag→doc, hybrid→both)

   Citation Quality:
     • 1 = No citations
     • 3 = Citations present but missing fields
     • 5 = Complete citations (source_id, title, url, snippet)
   ```

3. **Pass/Fail Logic**
   - PASS: correctness ≥3 AND groundedness ≥3 AND citations ≥3 AND latency ≤5000ms
   - FAIL: Any threshold not met

4. **Metrics Aggregation**
   - **Pass Rates:** By category (tool/rag/hybrid) and overall
   - **Quality Metrics:** Average scores across all dimensions
   - **Citations:** Percentage with citations, average count
   - **Latency:** Mean and p95 percentile

5. **V1 Readiness Gates**
   ```python
   MIN_PASS_RATE = 0.80              # 80% of 40 must pass
   MIN_CORRECTNESS = 3.5/5.0
   MIN_GROUNDEDNESS = 3.5/5.0
   MIN_CITATION_QUALITY = 3.5/5.0
   MIN_CITATION_PRESENT_RATE = 0.90  # 90% must have citations
   MAX_P95_LATENCY_MS = 5000         # 5 second p95
   ```

**Usage:**

```bash
python eval/eval_runner.py \
  --dataset eval/evaluation_dataset.json \
  --output eval/eval_results.json
```

**Output:**

- Console summary with pass rates and metrics
- JSON report with per-prompt scores and detailed analysis
- V1 readiness verdict (READY or NOT READY + failing criteria)

---

### T20: Framework Readiness ✅ COMPLETE (Testing Deferred)

**Status:** Framework is production-ready. Testing deferred pending API access.

**What's Ready:**

- ✅ Dataset with 40 representative prompts
- ✅ Scoring logic with objective criteria
- ✅ Report generation with metrics
- ✅ All code tested for syntax/imports
- ✅ Demo script showing framework operation

**What's Needed to Execute:**

- ⏳ `ANTHROPIC_API_KEY` (to call Claude)
- ⏳ `DATABASE_URL` (to initialize CopilotService)
- ⏳ Valid player/team IDs in Copilot context
- ⏳ Corpus ingestion (for RAG queries)

**Expected Outcomes (When Executed):**

- Pass rate report by category (tool/rag/hybrid)
- Quality score averages and distributions
- Latency analysis (mean and p95)
- List of failing prompts with reasons
- Clear GO/NO-GO verdict for v1 production

---

## How Evaluation Works

### Execution Flow

```
1. Load dataset → 40 prompts
2. For each prompt:
   a) Send to CopilotService.query()
   b) Receive: answer + citations + tools + latency
   c) Score on 3 dimensions (correctness, groundedness, citations)
   d) Check latency ≤5s
   e) Mark PASS or FAIL
3. Compute aggregates:
   - Pass rate by category
   - Quality metric averages
   - Citation statistics
   - Latency p95
4. Check v1 gates
5. Report results
```

### Scoring Example

**Prompt:** "What is the infield fly rule?"

**Expected:** "Explains the infield fly rule and when it applies"  
**Grounding:** "Must cite document source (rules_infield_fly)"

**Copilot Response:**

```
Answer: "The infield fly rule prevents infielders from intentionally
dropping easy popup plays to create double plays. It applies when..."

Citations: [
  {
    "source_id": "rules_infield_fly",
    "source_type": "document",
    "title": "Infield Fly Rule",
    "url": "https://www.mlb.com/official-information/rules/infield-fly-rule",
    "snippet": "The infield fly rule prevents..."
  }
]

Latency: 245ms
```

**Scoring:**

- ✅ Correctness: 5/5 (answer fully explains rule)
- ✅ Groundedness: 5/5 (cites document for RAG query)
- ✅ Citations: 5/5 (complete metadata)
- ✅ Latency: 245ms < 5000ms
- **RESULT: PASS** ✅

---

## Files Delivered

### Milestone 5 Artifacts

- `eval/evaluation_dataset.json` — 40-prompt dataset with metadata
- `eval/eval_runner.py` — Automated evaluation & scoring engine
- `eval/demo_eval_framework.py` — Demo showing how evaluation works
- `MILESTONE_5_STATUS.md` — Detailed tuning guide
- `AI_ENGINEERING_PLAN.md` — Original requirements (reference)

### Supporting Docs

- This file: Complete Milestone 5 summary

---

## Testing Roadmap (When API Access Available)

### Phase 1: Baseline Run

```bash
python eval/eval_runner.py \
  --dataset eval/evaluation_dataset.json \
  --output eval/eval_baseline.json
```

- Identifies pass rate and failing categories
- Shows latency distribution

### Phase 2: Analysis

- Which prompts failed? (tool/rag/hybrid)
- Common failure patterns?
- Latency bottleneck identified?

### Phase 3: Tuning Iterations

Based on Phase 2, adjust:

- **Prompt engineering** (`copilot_service.py` synthesis prompts)
- **RAG parameters** (rag_min_score, rag_top_k in config.py)
- **Model settings** (temperature, max_tokens)
- **Tool timeouts** if needed

### Phase 4: Re-evaluate

```bash
python eval/eval_runner.py \
  --dataset eval/evaluation_dataset.json \
  --output eval/eval_v2.json
```

- Measure improvement
- Rinse & repeat until all gates met

### Phase 5: Release

- Document final tuning parameters
- Create v1.0.0 release notes
- Tag git repository

---

## Success Criteria

### V1 Production Readiness (All Must Pass)

| Criterion            | Threshold | Why                                               |
| -------------------- | --------- | ------------------------------------------------- |
| Overall Pass Rate    | ≥80%      | 32 of 40 prompts must meet all quality thresholds |
| Avg Correctness      | ≥3.5/5.0  | Answers must be accurate and complete             |
| Avg Groundedness     | ≥3.5/5.0  | Answers must cite appropriate sources             |
| Avg Citation Quality | ≥3.5/5.0  | Citations must be complete and usable             |
| Citation Presence    | ≥90%      | 36 of 40 responses must include citations         |
| p95 Latency          | ≤5000ms   | 95% of responses within 5 second budget           |

### Interpretation

- **All criteria met** → ✅ v1.0 READY FOR PRODUCTION
- **Any criterion fails** → ❌ Requires tuning, return to Phase 3

---

## Key Insights

### Dataset Diversity

- **Tool queries** test live data accuracy and tool citation
- **RAG queries** test knowledge base coverage and doc grounding
- **Hybrid queries** test integration and synthesis quality
- Mix covers single-source and multi-source scenarios

### Scoring Philosophy

- **Heuristic approach** (keyword matching + metadata checks)
- Sufficient for automated triage of failing prompts
- In production: Could be upgraded to LLM-based grading
- Trade-off: Speed vs. deep semantic understanding

### Latency Budget

- Average response should be <2s
- p95 (worst 5%) at 5s allows for slow calls
- Breakdown: tool execution + model inference + retrieval

---

## Future Enhancements

### Evaluation v2 (Post-v1)

1. **LLM-Based Grading** — Use Claude to score correctness/groundedness
2. **Cost Tracking** — Measure API spend per query
3. **Error Case Coverage** — Add adversarial/edge case prompts
4. **Category Weights** — Different thresholds by category if needed
5. **Continuous Monitoring** — Dashboard tracking production performance

### Advanced Metrics

- Hallucination detection (fact-checking against corpus)
- Citation coverage (% of claims backed by citations)
- Response diversity (prompt engineering effectiveness)
- Cold-start performance (cache miss scenarios)

---

## Known Limitations

### Current Scope

- Evaluation framework only (no actual execution data)
- Heuristic scoring (limited semantic understanding)
- Assumes corpus is ingested (doesn't test ingestion)
- Doesn't test concurrent request handling
- No adversarial/jailbreak testing

### Future Scope

- Production monitoring (success metrics post-launch)
- User feedback integration
- A/B testing framework (prompt variants)
- Rollback procedures (version pinning)

---

## Deployment Notes

### Prerequisites

```bash
# Ensure dependencies installed
pip install -r requirements.txt

# Set environment
export ANTHROPIC_API_KEY=your_key
export DATABASE_URL=your_db_url

# Activate venv
source .venv/bin/activate
```

### Running Evaluation

```bash
# Full run (40 prompts)
python eval/eval_runner.py \
  --dataset eval/evaluation_dataset.json \
  --output eval/eval_results.json

# View results
cat eval/eval_results.json | jq '.overall_pass_rate, .v1_ready'
```

### Interpreting Output

```json
{
  "v1_ready": true,
  "overall_pass_rate": 0.87,
  "tool_pass_rate": 0.93,
  "rag_pass_rate": 0.8,
  "hybrid_pass_rate": 0.85,
  "avg_correctness": 4.1,
  "avg_groundedness": 4.2,
  "avg_citation_quality": 3.8,
  "citation_present_rate": 0.92,
  "avg_latency_ms": 1245,
  "p95_latency_ms": 3421
}
```

---

## Summary

**Milestone 5 has delivered a production-grade evaluation framework** that will objectively measure Copilot v1 readiness across three critical dimensions:

1. **Correctness** — Does it answer questions accurately?
2. **Groundedness** — Does it properly cite sources?
3. **Performance** — Is it fast enough?

The framework is **ready to execute immediately** when API access becomes available. It will provide clear GO/NO-GO verdict for production launch with detailed diagnostic data for any required tuning.

---

**Next Action:** When Anthropic API access is available, execute evaluation and begin Phase 2-3 (tuning) as needed.

**V1 Target:** All 6 v1 readiness criteria met → Production deployment.
