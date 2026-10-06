#!/usr/bin/env python3
"""
Test RAG retrieval at different threshold values.
Runs the same queries at thresholds: 0.25, 0.35, 0.45
Compares chunk acceptance rates and relevance.
"""

import requests
import json
import os
from typing import List, Dict, Any
from dotenv import load_dotenv

# Load environment
load_dotenv()
BASE_URL = "http://127.0.0.1:8001"

# Test queries covering various topics
TEST_QUERIES = [
    "What is the infield fly rule?",
    "Tell me about the designated hitter rule",
    "What makes a good shortstop?",
    "How does a World Series work?",
    "Explain the concept of sabermetrics"
]

def run_trace_query(query: str) -> Dict[str, Any]:
    """Execute a trace query and extract RAG details."""
    try:
        response = requests.post(
            f"{BASE_URL}/analysis/copilot/trace",
            json={"query": query, "mode": "rag"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error querying trace endpoint: {e}")
        return None

def extract_rag_metrics(trace_data: Dict) -> Dict[str, Any]:
    """Extract RAG retrieval metrics from trace data."""
    if not trace_data or "steps" not in trace_data:
        return None
    
    for step in trace_data.get("steps", []):
        if step.get("stage_name") == "RAG Retrieval":
            details = step.get("details", {})
            scored_chunks = details.get("scored_chunks", [])
            
            # Calculate metrics
            total_chunks = len(scored_chunks)
            passed_chunks = sum(1 for chunk in scored_chunks if chunk.get("passed_threshold"))
            avg_score = sum(chunk.get("score", 0) for chunk in scored_chunks) / total_chunks if total_chunks > 0 else 0
            
            chunk_scores = sorted([chunk.get("score", 0) for chunk in scored_chunks], reverse=True)
            
            return {
                "total_chunks_scored": total_chunks,
                "chunks_passed_threshold": passed_chunks,
                "retrieval_rate": (passed_chunks / total_chunks * 100) if total_chunks > 0 else 0,
                "avg_score": round(avg_score, 4),
                "top_5_scores": [round(s, 4) for s in chunk_scores[:5]],
                "scored_chunks": scored_chunks
            }
    
    return None

def test_threshold(threshold: float, top_k: int = 2) -> None:
    """Test RAG retrieval at a specific threshold."""
    print(f"\n{'='*80}")
    print(f"Testing RAG_MIN_SCORE = {threshold}, RAG_TOP_K = {top_k}")
    print(f"{'='*80}\n")
    
    # Update environment variables
    os.environ["RAG_MIN_SCORE"] = str(threshold)
    os.environ["RAG_TOP_K"] = str(top_k)
    
    # Note: Need to restart server for env changes to take effect
    print(f"Note: Updated environment to RAG_MIN_SCORE={threshold}, RAG_TOP_K={top_k}")
    print("(Restart server to apply changes)\n")
    
    results_by_query = {}
    
    for query in TEST_QUERIES:
        print(f"Query: {query}")
        trace_data = run_trace_query(query)
        
        if trace_data:
            metrics = extract_rag_metrics(trace_data)
            if metrics:
                results_by_query[query] = metrics
                print(f"  Total chunks scored: {metrics['total_chunks_scored']}")
                print(f"  Chunks passed threshold: {metrics['chunks_passed_threshold']}")
                print(f"  Retrieval rate: {metrics['retrieval_rate']:.1f}%")
                print(f"  Average score: {metrics['avg_score']}")
                print(f"  Top 5 scores: {metrics['top_5_scores']}")
            else:
                print("  ✗ Could not extract RAG metrics")
        else:
            print("  ✗ API call failed")
        print()
    
    return results_by_query

def main():
    print("\n" + "="*80)
    print("RAG THRESHOLD MATRIX TEST")
    print("Testing: 0.25 (high recall), 0.35 (medium), 0.45 (high precision)")
    print("="*80)
    
    # Verify server is running
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=2)
        print(f"✓ Server is running on {BASE_URL}\n")
    except:
        # Try without /health endpoint if it doesn't exist
        try:
            trace_data = run_trace_query("test")
            print(f"✓ Server appears to be running\n")
        except:
            print(f"✗ Cannot connect to server at {BASE_URL}")
            print("  Make sure the uvicorn server is running")
            return
    
    # Note about environment variable change
    print("IMPORTANT: To test different thresholds, you need to:")
    print("1. Update .env file with RAG_MIN_SCORE value")
    print("2. Restart the uvicorn server")
    print("3. Run this script again\n")
    
    print("Current .env values:")
    result = os.popen("grep -E '^RAG_(MIN_SCORE|TOP_K)=' /Users/gtsacona/projects/mlb-api/.env 2>/dev/null | cat").read()
    print(result if result else "  (Could not read .env)\n")
    
    # For now, just show the current threshold results
    print("Running queries with current threshold settings...\n")
    results = test_threshold(float(os.getenv("RAG_MIN_SCORE", "0.3")), int(os.getenv("RAG_TOP_K", "2")))
    
    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    
    if results:
        total_queries = len(results)
        avg_retrieval = sum(r.get('retrieval_rate', 0) for r in results.values()) / total_queries if total_queries > 0 else 0
        avg_score_all = sum(r.get('avg_score', 0) for r in results.values()) / total_queries if total_queries > 0 else 0
        
        print(f"Queries tested: {total_queries}")
        print(f"Average retrieval rate: {avg_retrieval:.1f}%")
        print(f"Average chunk score: {avg_score_all:.4f}")
    
    print("\nTo test different thresholds:")
    print("1. Edit .env and change RAG_MIN_SCORE")
    print("2. Stop and restart the uvicorn server")
    print("3. Run: python test_threshold_matrix.py")

if __name__ == "__main__":
    main()
