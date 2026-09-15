# Copilot Query Tracing Guide

When running the server, you can trace RAG and LLM behavior through the `/analysis/copilot/trace` endpoint. This gives you complete visibility into:

- **Query Analysis**: How the query is tokenized
- **Mode Selection**: Whether it was routed to tool/RAG/hybrid
- **Tool Selection**: Which API was selected (if applicable)
- **RAG Retrieval**: How many corpus chunks were retrieved
- **Prompt Construction**: What was sent to the LLM
- **LLM Synthesis**: Token counts and latency from Claude
- **Citations & Confidence**: How the response was grounded
- **Timing Breakdown**: Latency at each stage

## Quick Start

Start the server:

```bash
source venv/bin/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

### Trace a RAG Query

```bash
curl -X POST http://127.0.0.1:8001/analysis/copilot/trace \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the infield fly rule?", "mode": "rag"}' | python -m json.tool
```

**Response shows:**

- Step 1: Query tokenized into 6 tokens
- Step 2: RAG mode selected
- Step 4: Retrieved 3 corpus chunks from knowledge base
- Step 5: Prompt constructed with context blocks
- Step 6: LLM synthesized response (244 tokens, 81ms)
- Step 7: Generated 3 citations (documents)
- Step 8: Total latency 82ms

### Trace a Tool Query

```bash
curl -X POST http://127.0.0.1:8001/analysis/copilot/trace \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Get player stats",
    "mode": "tool",
    "context": {"player_id": 545361, "season": 2024}
  }' | python -m json.tool
```

**Response shows:**

- Step 1: Query tokenized
- Step 2: Tool mode selected
- Step 3: Tool selected (`get_player_stat_split` with inputs)
- Step 5: Prompt constructed with tool context
- Step 6: Tool invoked (221ms)
- Step 7: Generated tool citation
- Step 8: Total latency 222ms

### Trace a Hybrid Query

```bash
curl -X POST http://127.0.0.1:8001/analysis/copilot/trace \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Tell me about the player and also compare to league rules",
    "mode": "hybrid",
    "context": {"player_id": 545361, "season": 2024}
  }' | python -m json.tool
```

**Response shows:**

- Tool invoked (Step 3-6)
- RAG retrieval in parallel (Step 4)
- LLM synthesis combining both (Step 6)
- Citations from both sources (Step 7)

## Auto Mode

Let the system pick the best mode:

```bash
curl -X POST http://127.0.0.1:8001/analysis/copilot/trace \
  -H "Content-Type: application/json" \
  -d '{"query": "What is the infield fly rule?"}' | python -m json.tool
```

## Response Structure

Each trace response contains:

```json
{
  "request_id": "<unique-id>",
  "query": "...",
  "mode": "rag|tool|hybrid",
  "steps": [
    {
      "step_number": 1,
      "stage_name": "Query Analysis",
      "description": "What happened",
      "details": {
        /* stage-specific data */
      },
      "timing_ms": 0
    }
    // ... 8 steps total
  ],
  "total_ms": 82,
  "final_response": {
    "answer": "...",
    "confidence": "high|medium|low",
    "citations": 3,
    "tools_used": ["get_player_stat_split"],
    "warnings": ["Mock LLM mode enabled; synthesis output is simulated"]
  }
}
```

## Understanding Latency Breakdown

The trace shows timing at each step:

- **Step 1-2**: Query analysis & mode selection (~0-1ms, usually negligible)
- **Step 3**: Tool selection (~0ms)
- **Step 4**: RAG retrieval (~0-5ms, pre-vectorized)
- **Step 5**: Prompt construction (~0-1ms)
- **Step 6**: LLM synthesis (typically 50-200ms)
- **Step 7**: Citations & confidence (~0-1ms)

For tool queries, the `total_latency_breakdown` in Step 8 shows:

- `orchestration_ms`: Query routing overhead
- `tools_ms`: Tool execution time
- `retrieval_ms`: RAG retrieval time
- `model_ms`: LLM latency

## Key Differences: RAG vs. Tool vs. Hybrid

| Aspect              | RAG                   | Tool           | Hybrid          |
| ------------------- | --------------------- | -------------- | --------------- |
| **Data Source**     | Knowledge base chunks | External APIs  | Both            |
| **Step 3**          | Skipped               | Tool selection | Tool selection  |
| **Step 4**          | Chunk retrieval       | Skipped        | Chunk retrieval |
| **Citations**       | Documents             | Tools          | Both            |
| **Latency Profile** | Model-dominant        | Tool-dominant  | Balanced        |

## Debugging Tips

1. **Query not routed correctly?**
   - Check Step 2 for mode selection
   - Check if context has required fields for tool mode

2. **Tool not selected?**
   - Check Step 3 details for which tools matched
   - Verify `player_id`, `season`, etc. are in context

3. **Low confidence answer?**
   - Check Step 7 for citation count
   - Check Step 4 for RAG retrieval count

4. **Slow response?**
   - Check `total_ms` in Step 8
   - Look at `latency_breakdown` to identify bottleneck

## Using Both Endpoints

The API has two copilot endpoints:

- **`POST /analysis/copilot/query`**: Regular endpoint, returns just the answer + citations
- **`POST /analysis/copilot/trace`**: Debug endpoint, returns full execution trace

Both accept the same request format and produce consistent results. Use `trace` when you need visibility; use `query` for production.

## Mock Mode Notice

Currently running in **mock LLM mode** (for development without network access).
All responses show: `"Mock LLM mode enabled; synthesis output is simulated"`

When connected to real Anthropic API:

- Responses will use actual Claude models
- Token counts will reflect real API usage
- Latencies will show real network roundtrips
