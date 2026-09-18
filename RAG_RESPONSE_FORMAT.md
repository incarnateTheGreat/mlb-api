# RAG Orchestration Response Format

## T14: RAG-Only Orchestration Path

This document specifies the response contract for RAG-only queries (mode="rag").

## Response Structure

All RAG responses follow the `CopilotResponse` schema defined in [models/analysis.py](app/models/analysis.py):

```python
class CopilotResponse(BaseModel):
    request_id: str              # Unique trace ID
    answer: str                  # Natural language response
    citations: list[Citation]    # Evidence sources
    tools_used: list[ToolCall]   # (empty for RAG-only)
    confidence: str              # "high", "medium", "low"
    model_info: ModelInfo        # LLM metadata
    timing: TimingInfo           # Latency breakdown
    warnings: list[str]          # Uncertainty/limitation notes
```

## Citation Format

Each citation object includes:

```json
{
  "source_id": "rules_infield_fly",
  "source_type": "document",
  "title": "Infield Fly Rule",
  "url": "https://www.mlb.com/official-information/rules/infield-fly-rule",
  "snippet": "An infield fly is a fair fly ball that an infielder can catch with ordinary effort.",
  "section": "Rule 5.09(a)(5)"
}
```

### Citation Requirements

- ✓ **source_id**: Maps to corpus whitelist (see CORPUS_POLICY.md)
- ✓ **source_type**: Always "document" for RAG sources
- ✓ **title**: Human-readable source name (from whitelist)
- ✓ **url**: HTTPS link to original source (from whitelist)
- ✓ **snippet**: Relevant excerpt, 50-200 characters
- ✓ **section**: Document section/heading where claim appears

## Confidence Levels

RAG responses are classified by confidence:

| Confidence | Criteria                                             | Example                                  |
| ---------- | ---------------------------------------------------- | ---------------------------------------- |
| **high**   | ≥2 relevant chunks, avg score >0.5, clear grounding  | Rule definitions, official statistics    |
| **medium** | 1 relevant chunk, score 0.35-0.5, partially grounded | Historical facts, contextual information |
| **low**    | 1 weak chunk, score <0.35, limited grounding         | Edge cases, rare topics                  |
| **none**   | No relevant chunks found                             | Queries outside knowledge base           |

## Response Examples

### Example 1: High-Confidence Rule Query

**Request:**

```json
{
  "query": "What is the infield fly rule?",
  "mode": "rag"
}
```

**Response:**

```json
{
  "request_id": "req_abc123",
  "answer": "The infield fly rule applies when there are runners on first and second (or bases loaded) with fewer than two outs. An infield fly is a fair fly ball that an infielder can catch with ordinary effort. When the rule is in effect, the batter is automatically out regardless of whether the ball is caught.",
  "citations": [
    {
      "source_id": "rules_infield_fly",
      "source_type": "document",
      "title": "Infield Fly Rule",
      "url": "https://www.mlb.com/official-information/rules/infield-fly-rule",
      "snippet": "An infield fly is a fair fly ball that an infielder can catch with ordinary effort.",
      "section": "Rule 5.09(a)(5)"
    }
  ],
  "tools_used": [],
  "confidence": "high",
  "model_info": {
    "provider": "anthropic",
    "model_name": "claude-3-5-sonnet-20241022",
    "prompt_tokens": 250,
    "completion_tokens": 85,
    "total_tokens": 335
  },
  "timing": {
    "total_ms": 1245,
    "orchestration_ms": 50,
    "tools_ms": 0,
    "retrieval_ms": 125,
    "model_ms": 1050,
    "synthesis_ms": 20
  },
  "warnings": []
}
```

### Example 2: Low-Confidence / No Results

**Request:**

```json
{
  "query": "Who is the best shortstop in baseball?",
  "mode": "rag"
}
```

**Response:**

```json
{
  "request_id": "req_def456",
  "answer": "I don't have reliable information about contemporary player rankings in my knowledge base.",
  "citations": [],
  "tools_used": [],
  "confidence": "none",
  "model_info": {
    "provider": "anthropic",
    "model_name": "claude-3-5-sonnet-20241022",
    "prompt_tokens": 150,
    "completion_tokens": 20,
    "total_tokens": 170
  },
  "timing": {
    "total_ms": 350,
    "orchestration_ms": 30,
    "tools_ms": 0,
    "retrieval_ms": 100,
    "model_ms": 200,
    "synthesis_ms": 20
  },
  "warnings": [
    "No relevant documents found (RAG retrieval returned 0 chunks at threshold 0.30)",
    "Consider using tool mode for player stats or statistics queries"
  ]
}
```

## Response Construction Pipeline

### Step 1: Orchestration Decision

- User query received with mode="rag"
- Decide to use RAG-only path

### Step 2: RAG Retrieval

- Vectorize user query
- Score all corpus chunks (semantic similarity)
- Filter by threshold (RAG_MIN_SCORE=0.30)
- Select top-k chunks (RAG_TOP_K=2)
- Collect source metadata for citations

### Step 3: LLM Synthesis

- Construct prompt with top-k chunks as context
- Call Claude API with temperature/max_tokens
- Get natural language response

### Step 4: Citation Building

- Extract claims from LLM response
- Map to source chunks (by chunk_id)
- Build Citation objects with title, URL, snippet, section
- Apply confidence classification

### Step 5: Response Formatting

- Populate CopilotResponse struct
- Include timing breakdowns
- Add warnings if confidence is low
- Return as JSON

## Warnings

RAG responses include warnings in these cases:

1. **Low Retrieval**: No chunks passed threshold

   ```
   "No relevant documents found (RAG retrieval returned 0 chunks at threshold 0.30)"
   ```

2. **Low Score**: Only weak matches found

   ```
   "Retrieved chunks have low relevance scores (max: 0.38; threshold: 0.30)"
   ```

3. **Hallucination Risk**: LLM answered beyond corpus scope

   ```
   "LLM response contains claims not grounded in retrieved documents"
   ```

4. **Recommendation**: Suggest tool mode
   ```
   "Consider using tool mode for player stats or live game data"
   ```

## Production Checklist

For production readiness:

- ✓ All responses include valid Citations or empty list
- ✓ Confidence level matches retrieval quality
- ✓ URLs in citations are HTTPS and resolvable
- ✓ Snippets are 50-200 characters
- ✓ Warnings are present when confidence < "high"
- ✓ Timing fields sum to approximately total_ms
- ✓ request_id is globally unique and traceable

## Testing

To test RAG responses locally:

```bash
# Terminal 1: Start server
source venv/bin/activate
python -m uvicorn app.main:app --port 8001

# Terminal 2: Send RAG query
curl -X POST http://127.0.0.1:8001/analysis/copilot/query \
  -H "Content-Type: application/json" \
  -d '{"query":"What is the infield fly rule?","mode":"rag"}' \
  | jq .

# Or with tracing to see all steps:
curl -X POST http://127.0.0.1:8001/analysis/copilot/trace \
  -H "Content-Type: application/json" \
  -d '{"query":"What is the infield fly rule?","mode":"rag"}' \
  | jq '.steps[] | select(.stage_name=="RAG Retrieval")'
```

## See Also

- [CORPUS_POLICY.md](CORPUS_POLICY.md) — Source whitelist and metadata requirements
- [THRESHOLD_ANALYSIS.md](THRESHOLD_ANALYSIS.md) — RAG threshold tuning results
- [app/models/analysis.py](app/models/analysis.py) — Response model definitions
- [scripts/ingest_corpus.py](scripts/ingest_corpus.py) — Corpus management tool
