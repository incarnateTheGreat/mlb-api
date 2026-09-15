#!/usr/bin/env python
"""
Trace a query through the copilot system end-to-end.

Shows:
1. Request creation
2. Mode inference
3. RAG retrieval with scoring details
4. Citation building
5. LLM synthesis (or mock)
6. Final response
"""

import asyncio
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.models.analysis import CopilotRequest
from app.services.copilot_service import CopilotService
from app.services.rag_service import get_rag_service


def print_section(title: str):
    """Print a section header."""
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


def print_kv(key: str, value):
    """Print key-value pair."""
    if isinstance(value, (list, dict)):
        print(f"{key}:")
        print(json.dumps(value, indent=2, default=str))
    else:
        print(f"{key}: {value}")


async def trace_query(query: str, mode: str = "auto", context: dict = None):
    """Trace a query through the system."""
    
    print_section("QUERY TRACING DEMO")
    print(f"Query: {query}")
    print(f"Mode: {mode}")
    print(f"Context: {context}")
    
    # =========================================================================
    # STEP 1: Create request
    # =========================================================================
    print_section("STEP 1: Create Request")
    
    request = CopilotRequest(
        query=query,
        mode=mode,
        context=context or {},
        max_tokens=1024,
        temperature=0.7,
    )
    print(f"Request ID will be assigned during query()")
    print_kv("Query", request.query)
    print_kv("Mode", request.mode)
    print_kv("Context", request.context)
    
    # =========================================================================
    # STEP 2: Initialize copilot service
    # =========================================================================
    print_section("STEP 2: Initialize Copilot Service")
    
    service = CopilotService()
    print(f"Copilot model: {service.model}")
    print(f"Mock LLM enabled: {service.mock_llm_enabled}")
    print(f"RAG top-k: {service.settings.rag_top_k}")
    print(f"RAG min-score threshold: {service.settings.rag_min_score}")
    
    # =========================================================================
    # STEP 3: Infer mode (if auto)
    # =========================================================================
    print_section("STEP 3: Mode Inference (if auto)")
    
    if request.mode == "auto":
        inferred_mode = service._infer_mode(request.query)
        print(f"Query keywords detected → Mode inferred as: {inferred_mode}")
    else:
        inferred_mode = request.mode
        print(f"Mode explicitly set to: {inferred_mode}")
    
    # =========================================================================
    # STEP 4: RAG Retrieval (if rag or hybrid)
    # =========================================================================
    if inferred_mode in {"rag", "hybrid"}:
        print_section("STEP 4: RAG Retrieval")
        
        rag_service = get_rag_service()
        
        # Show corpus state
        print("RAG Service State:")
        rag_service.ensure_ingested()
        print(f"  Corpus chunks loaded: {len(rag_service._chunks)}")
        print(f"  Chunks by file:")
        file_counts = {}
        for chunk in rag_service._chunks:
            file_counts[chunk.source_id] = file_counts.get(chunk.source_id, 0) + 1
        for source_id, count in sorted(file_counts.items()):
            print(f"    {source_id}: {count} chunks")
        
        # Vectorize query
        print(f"\nQuery Vectorization:")
        qvec = rag_service._vectorize(request.query)
        print(f"  Input: '{request.query}'")
        print(f"  Tokens extracted: {list(qvec.keys())}")
        print(f"  Vector (term-frequency normalized): {qvec}")
        
        # Score all chunks
        print(f"\nScoring All Chunks:")
        all_scores = []
        for chunk in rag_service._chunks:
            score = rag_service._cosine_similarity(qvec, chunk.vector)
            all_scores.append({
                "chunk_id": chunk.chunk_id,
                "title": chunk.title,
                "score": round(score, 3),
                "passed_threshold": score >= rag_service.min_score,
            })
        
        all_scores.sort(key=lambda x: x["score"], reverse=True)
        for item in all_scores:
            marker = "✓" if item["passed_threshold"] else "✗"
            print(f"  {marker} {item['chunk_id']:30s} score={item['score']:.3f} ({item['title']})")
        
        # Retrieve top-k
        print(f"\nRetrieving Top-K (k={service.settings.rag_top_k}, threshold={rag_service.min_score}):")
        retrieval_results = rag_service.retrieve(request.query, top_k=service.settings.rag_top_k)
        print(f"  Retrieved {len(retrieval_results)} chunks:")
        for i, result in enumerate(retrieval_results, 1):
            print(f"\n  [{i}] {result.title}")
            print(f"      Source ID: {result.source_id}")
            print(f"      URL: {result.url}")
            print(f"      Score: {result.score:.3f}")
            print(f"      Snippet: {result.snippet[:100]}...")
        
        # =========================================================================
        # STEP 5: Build Citations
        # =========================================================================
        print_section("STEP 5: Build Citations from Retrieved Chunks")
        
        citations = []
        for i, item in enumerate(retrieval_results, 1):
            citation = {
                "source_id": item.source_id,
                "source_type": "document",
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "section": item.section,
            }
            citations.append(citation)
            print(f"Citation {i}:")
            print(f"  Title: {citation['title']}")
            print(f"  URL: {citation['url']}")
        
        # =========================================================================
        # STEP 6: LLM Synthesis
        # =========================================================================
        print_section("STEP 6: LLM Synthesis (Context Building)")
        
        context_blocks = []
        for index, item in enumerate(retrieval_results, start=1):
            block = f"[{index}] Title: {item.title}\nURL: {item.url}\nSection: {item.section}\nSnippet: {item.snippet}"
            context_blocks.append(block)
        
        prompt = (
            "You are a baseball analyst. Use only the provided sources to answer the user query. "
            "If evidence is weak, say you are uncertain. Keep the answer concise.\n\n"
            f"User query: {request.query}\n\n"
            "Sources:\n"
            + "\n\n".join(context_blocks)
        )
        
        print(f"Prompt being sent to LLM (length: {len(prompt)} chars):")
        print("-" * 70)
        print(prompt)
        print("-" * 70)
    
    # =========================================================================
    # STEP 7: Run full query through copilot service
    # =========================================================================
    print_section("STEP 7: Full Query Execution")
    
    response = await service.query(request)
    
    print(f"Request ID: {response.request_id}")
    print(f"Query Mode: {response.query_mode}")
    print(f"Confidence: {response.confidence}")
    print(f"\nAnswer:")
    print(f"  {response.answer}")
    
    # =========================================================================
    # STEP 8: Response Metadata
    # =========================================================================
    print_section("STEP 8: Response Metadata & Telemetry")
    
    print(f"Citations (count: {len(response.citations)}):")
    for i, citation in enumerate(response.citations, 1):
        print(f"  {i}. [{citation.source_type}] {citation.title}")
        if citation.url:
            print(f"     URL: {citation.url}")
    
    print(f"\nTiming Breakdown:")
    print(f"  Total: {response.timing.total_ms}ms")
    print(f"  Orchestration: {response.timing.orchestration_ms}ms")
    print(f"  Tools: {response.timing.tools_ms}ms")
    print(f"  Retrieval: {response.timing.retrieval_ms}ms")
    print(f"  Model: {response.timing.model_ms}ms")
    
    print(f"\nModel Info:")
    print(f"  Provider: {response.model_info.provider}")
    print(f"  Model: {response.model_info.model_name}")
    print(f"  Prompt tokens: {response.model_info.prompt_tokens}")
    print(f"  Completion tokens: {response.model_info.completion_tokens}")
    print(f"  Total tokens: {response.model_info.total_tokens}")
    
    if response.warnings:
        print(f"\nWarnings (count: {len(response.warnings)}):")
        for warning in response.warnings:
            print(f"  ⚠ {warning}")
    
    print_section("TRACE COMPLETE")


async def main():
    # Example queries to trace
    examples = [
        ("What is the infield fly rule?", "auto", None),
        ("Get player 545361's stats for 2024", "auto", {"player_id": 545361, "season": 2024}),
    ]
    
    print("\n" + "="*70)
    print("  COPILOT QUERY TRACER")
    print("="*70)
    print("\nAvailable examples:")
    for i, (q, m, ctx) in enumerate(examples, 1):
        print(f"  {i}. {q}")
    
    # Use first example or user input
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        await trace_query(query)
    else:
        # Trace first example
        query, mode, context = examples[0]
        await trace_query(query, mode, context)


if __name__ == "__main__":
    asyncio.run(main())
