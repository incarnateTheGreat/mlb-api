# Milestone 5: Evaluation and Readiness

**Status:** In Progress (T18, T19 Complete | T20 Pending)  
**Date:** September 16, 2026

## Overview

Milestone 5 focuses on evaluating the Copilot system against production readiness criteria and tuning parameters to meet quality thresholds.

## Tickets Status

### T18: Build 40-Prompt Evaluation Dataset ✅ COMPLETE

**Deliverables:**

1. **Comprehensive Evaluation Dataset** (`eval/evaluation_dataset.json`)
   - 40 total prompts split into three categories:
     - **15 Tool-Only Queries:** Focus on live MLB data (scores, player stats, schedules)
     - **15 RAG-Only Queries:** Focus on rules, history, and analytical concepts
     - **10 Hybrid Queries:** Combine live data with historical/contextual information

**Dataset Structure:**
Each prompt includes:

```json
{
  "id": "tool_001",
  "query": "What was the score of the Yankees vs Orioles game?",
  "context": { "focus": "live_score" },
  "expected_answer": "Contains score for Yankees vs Orioles",
  "grounding_rules": [
    "Must cite tool:get_game_summary",
    "Score must be from StatsAPI"
  ],
  "acceptance_criteria": {
    "correctness": "high",
    "has_citations": true,
    "min_citation_count": 1
  }
}
```

**Dataset Categories:**

**Tool Queries (15):**

- `tool_001-015`: Game scores, player stats (HR, AVG, RBI, ERA, K), team schedules
- Covers: Current season data, multiple positions (hitter/pitcher), date ranges
- Expected source: Always `tool:get_game_summary`, `tool:get_player_stat_split`, `tool:get_team_schedule`

**RAG Queries (15):**

- `rag_001-015`: Rules (infield fly, DH, strikes, double play), history, records, analytics
- Covers: Definitions, historical facts, sabermetrics (WAR, OPS), league structure
- Expected source: Always document corpus (rules, history, stats sabermetrics)

**Hybrid Queries (10):**

- `hybrid_001-010`: Combine live stats with historical context
  - Examples: "2024 HR total and historical rank", "Today's score and why streaks matter", "Current ERA and how analytics evaluate pitchers"
- Expected: Mix of tool + document citations

**Acceptance Criteria:**

- Tool queries: Must cite tool source, high correctness expected
- RAG queries: Must cite document source, high correctness expected
- Hybrid queries: Must cite both tool AND document, balanced synthesis
- All: Citation presence required, substantive answers

---

### T19: Eval Runner and Score Report ✅ COMPLETE

**Deliverable:**
`eval/eval_runner.py` — Automated evaluation script with heuristic scoring

**Features:**

1. **Batch Evaluation:**
   - Runs all 40 prompts sequentially against CopilotService
   - Tracks latency per prompt
   - Captures full CopilotResponse (answer, citations, tools_used, warnings)

2. **Heuristic Scoring (1-5 scale):**
   - **Correctness:** Does answer contain expected information?
     - Checks if key phrases from expected_answer appear in response
     - Bonus for substantive responses (>50 chars)
   - **Groundedness:** Are appropriate citations provided?
     - Tool queries: Must cite tool sources
     - RAG queries: Must cite document sources
     - Hybrid queries: Must cite both types
   - **Citation Quality:** Are citations complete and usable?
     - Checks for source_id, title, URL presence
     - Penalizes if citation count < required (2 for hybrid, 1 for others)

3. **Metrics Computation:**
   - **Pass Rate:** % of prompts meeting all thresholds (correctness ≥3, groundedness ≥3, citations ≥3, latency ≤5s)
   - **Average Quality:** Mean scores for correctness, groundedness, citation quality
   - **Citation Statistics:** % of responses with citations, average count per response
   - **Latency:** Average and p95 percentile

4. **V1 Readiness Gates:**

   ```python
   MIN_PASS_RATE = 0.80              # 80% of prompts must pass
   MIN_CORRECTNESS = 3.5/5.0         # Average correctness score
   MIN_GROUNDEDNESS = 3.5/5.0        # Average groundedness score
   MIN_CITATION_QUALITY = 3.5/5.0    # Average citation quality
   MIN_CITATION_PRESENT_RATE = 0.90  # 90% of responses must have citations
   MAX_P95_LATENCY_MS = 5000         # 5 second p95 latency
   ```

5. **Output:**
   - Console summary with pass rates by category
   - JSON report (`eval/eval_results.json`) with all scores and metrics
   - v1 readiness verdict (ready/not ready + failing criteria)

**Usage:**

```bash
# Run evaluation
python eval/eval_runner.py \
  --dataset eval/evaluation_dataset.json \
  --output eval/eval_results.json

# Output example:
# ======================================================================
# EVALUATION REPORT
# ======================================================================
# Total Prompts: 40 (Tool: 15, RAG: 15, Hybrid: 10)
#
# PASS RATES:
#   Tool:    93%
#   RAG:     87%
#   Hybrid:  80%
#   Overall: 87%
#
# QUALITY METRICS:
#   Avg Correctness:        4.1/5.0
#   Avg Groundedness:       4.3/5.0
#   Avg Citation Quality:   3.9/5.0
#
# LATENCY:
#   Average:                1245ms
#   p95:                    3421ms
#
# V1 READINESS:
#   ✅ READY FOR PRODUCTION
# ======================================================================
```

---

### T20: Quality Tuning and v1 Gate ⏳ IN PROGRESS

**Objective:** Tune system to meet v1 readiness thresholds and prepare for production release.

**Deliverables (Planned):**

1. **Prompt Engineering Iterations**
   - Run eval_runner.py
   - Analyze failing prompts
   - Tune copilot_service.py synthesis prompts
   - Re-run and measure improvement

2. **Retrieval Optimization**
   - If RAG queries underperforming, adjust:
     - `rag_min_score`: Lower threshold to retrieve more docs
     - `rag_top_k`: Increase from 4 to 5-6
     - Re-index corpus if needed
   - Re-run RAG subset of eval dataset

3. **Model Parameter Tuning**
   - Adjust `copilot_temperature` (currently 0.7)
   - Try different `copilot_max_tokens` settings
   - Consider fallback model for edge cases

4. **Latency Optimization**
   - Profile slowest queries
   - Increase tool/model timeouts if safe
   - Cache more aggressively

5. **v1 Release Gate**
   - Confirm all criteria met:
     - ✅ `overall_pass_rate >= 0.80`
     - ✅ `avg_correctness >= 3.5/5.0`
     - ✅ `avg_groundedness >= 3.5/5.0`
     - ✅ `avg_citation_quality >= 3.5/5.0`
     - ✅ `citation_present_rate >= 0.90`
     - ✅ `p95_latency_ms <= 5000`
   - Document final tuning parameters
   - Create v1.0.0 release note

---

## How to Run Evaluation

### Prerequisites

```bash
# Ensure .venv is activated
source .venv/bin/activate

# Ensure config is set
export ANTHROPIC_API_KEY=your_key
export DATABASE_URL=your_db_url
```

### Run Full Evaluation

```bash
cd /Users/gtsacona/projects/mlb-api
python eval/eval_runner.py \
  --dataset eval/evaluation_dataset.json \
  --output eval/eval_results.json
```

### Analyze Results

```bash
# View results JSON
cat eval/eval_results.json | python -m json.tool | less

# Extract just pass rates
cat eval/eval_results.json | jq '.tool_pass_rate, .rag_pass_rate, .hybrid_pass_rate, .v1_ready'
```

---

## Tuning Strategy (T20)

### If Tool Queries Fail

- Check if tool execution is working (verify tool logs)
- Ensure game_pk, player_id, team_id are valid in eval data
- Adjust expected_answer in dataset if StatsAPI format differs

### If RAG Queries Fail

- Review which documents were retrieved
- If none retrieved: Lower `rag_min_score` from 0.45 to 0.35
- If wrong docs: Improve corpus or update query formulation
- Re-ingest corpus if source documents were missing

### If Hybrid Queries Fail

- Check if synthesis prompt is clear enough
- Verify document + tool context both available in prompt
- Increase `copilot_max_tokens` if truncated
- Review failed cases manually for synthesis issues

### If Latency Too High (p95 > 5s)

- Profile which stage is slow (tool vs. model vs. retrieval)
- Increase timeouts? Or optimize queries?
- Consider async retrieval during tool execution
- Cache more aggressively

---

## Metrics Explanation

**Correctness (1-5):**

- 1 = Empty/completely wrong answer
- 3 = Answer contains some expected information
- 5 = Answer directly addresses expected outcome with detail

**Groundedness (1-5):**

- 1 = No citations or wrong source type
- 3 = Citations present but may be incomplete/irrelevant
- 5 = Citations perfectly match category (tool→tool sources, rag→docs, hybrid→both)

**Citation Quality (1-5):**

- 1 = No citations
- 3 = Citations present but missing fields (source_id, title, URL)
- 5 = Complete, well-formed citations with all metadata

**Pass (Boolean):**

- All of: correctness ≥3, groundedness ≥3, citations ≥3, latency ≤5000ms

---

## Files Created/Modified

### New Files

- `eval/evaluation_dataset.json` — 40-prompt evaluation dataset
- `eval/eval_runner.py` — Automated evaluation runner
- `MILESTONE_5_STATUS.md` — This file

### Will Modify (T20)

- `app/services/copilot_service.py` — Tune prompts
- `app/config.py` — Adjust RAG/model parameters
- `eval/evaluation_dataset.json` — Update expected answers if needed

---

## Next Steps (T20)

1. **Run Initial Evaluation:**

   ```bash
   python eval/eval_runner.py --dataset eval/evaluation_dataset.json --output eval/eval_baseline.json
   ```

2. **Analyze Results:**
   - Identify failing categories (tool/rag/hybrid)
   - Note which specific queries underperform
   - Check latency bottleneck

3. **Tune Iteratively:**
   - Make targeted changes (prompt, RAG threshold, temp)
   - Re-run evaluation
   - Track improvement per iteration

4. **Release When Ready:**
   - All v1 gates met
   - Create RELEASE_NOTES.md
   - Tag v1.0.0 in git

---

## Success Criteria

✅ Copilot v1 ready when:

- 80%+ pass rate across all prompts
- Average quality scores ≥3.5 in all dimensions
- 90%+ citation presence
- p95 latency ≤5 seconds
- No unhandled exceptions

**Status:** Awaiting T20 tuning phase.
