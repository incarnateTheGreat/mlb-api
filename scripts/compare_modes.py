#!/usr/bin/env python
"""Compare RAG vs Tool mode execution flows."""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.models.analysis import CopilotRequest
from app.services.copilot_service import CopilotService


async def trace_rag():
    """RAG mode: Knowledge base retrieval."""
    service = CopilotService()
    
    print("\n" + "="*70)
    print("RAG MODE: Knowledge Base Retrieval")
    print("="*70)
    
    request = CopilotRequest(
        query="What is the infield fly rule?",
        mode="rag",
    )
    
    print(f"\n📝 Query: {request.query}")
    print(f"🎯 Mode: {request.mode}")
    
    # Ensure corpus is loaded
    service.rag_service.ensure_ingested()
    num_chunks = len(service.rag_service._chunks)
    print(f"\n📚 Corpus State: {num_chunks} chunks loaded")
    
    # Run query
    print("\n⏳ Executing query...")
    response = await service.query(request)
    
    print(f"\n✅ Answer:")
    print(f"   {response.answer[:100]}...")
    print(f"\n📊 Metrics:")
    print(f"   • Confidence: {response.confidence}")
    print(f"   • Citations: {len(response.citations)}")
    for i, c in enumerate(response.citations):
        print(f"     [{i+1}] {c.source_id} ({c.source_type})")
    print(f"   • Latency: {response.timing.total_ms}ms")
    print(f"     - Retrieval: {response.timing.retrieval_ms}ms")
    print(f"     - Model: {response.timing.model_ms}ms")
    print(f"   • Tokens: {response.model_info.total_tokens} total")


async def trace_tool():
    """Tool mode: Deterministic API calls."""
    service = CopilotService()
    
    print("\n" + "="*70)
    print("TOOL MODE: Deterministic API Calls")
    print("="*70)
    
    request = CopilotRequest(
        query="What are the stats for player 545361?",
        mode="tool",
        context={"player_id": 545361, "season": 2024},
    )
    
    print(f"\n📝 Query: {request.query}")
    print(f"🎯 Mode: {request.mode}")
    print(f"📦 Context: {request.context}")
    
    # Show tool selection
    print(f"\n🔧 Tool Selection:")
    selected = service._select_tool(request)
    if selected:
        tool_name, tool_inputs = selected
        print(f"   • Tool: {tool_name}")
        print(f"   • Inputs: {tool_inputs}")
    
    # Run query
    print("\n⏳ Executing query...")
    response = await service.query(request)
    
    print(f"\n✅ Answer:")
    print(f"   {response.answer}")
    print(f"\n📊 Metrics:")
    print(f"   • Confidence: {response.confidence}")
    print(f"   • Tools invoked: {[t.tool_name for t in response.tools_used]}")
    print(f"   • Latency: {response.timing.total_ms}ms")
    print(f"     - Tool: {response.timing.tools_ms}ms")
    print(f"     - Model synthesis: {response.timing.model_ms}ms")
    print(f"   • Tokens: {response.model_info.total_tokens} total")


async def trace_hybrid():
    """Hybrid mode: Tools first, then RAG for context."""
    service = CopilotService()
    
    print("\n" + "="*70)
    print("HYBRID MODE: Tool Data + Knowledge Base Context")
    print("="*70)
    
    request = CopilotRequest(
        query="Tell me about the player's 2024 season and compare to league rules.",
        mode="hybrid",
        context={"player_id": 545361, "season": 2024},
    )
    
    print(f"\n📝 Query: {request.query}")
    print(f"🎯 Mode: {request.mode}")
    print(f"📦 Context: {request.context}")
    
    # Run query
    print("\n⏳ Executing query...")
    response = await service.query(request)
    
    print(f"\n✅ Answer:")
    print(f"   {response.answer[:120]}...")
    print(f"\n📊 Metrics:")
    print(f"   • Confidence: {response.confidence}")
    print(f"   • Tools invoked: {[t.tool_name for t in response.tools_used]}")
    print(f"   • RAG citations: {len(response.citations)}")
    print(f"   • Latency: {response.timing.total_ms}ms")
    print(f"     - Tool: {response.timing.tools_ms}ms")
    print(f"     - Retrieval: {response.timing.retrieval_ms}ms")
    print(f"     - Model synthesis: {response.timing.model_ms}ms")
    print(f"   • Tokens: {response.model_info.total_tokens} total")


async def main():
    print("\n" + "🔍 COPILOT QUERY MODE COMPARISON".center(70))
    print("Showing how the system routes queries through different execution paths\n")
    
    await trace_rag()
    await trace_tool()
    await trace_hybrid()
    
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print("""
RAG Mode:
  • Retrieves from knowledge base (rules, history)
  • Returns citations for transparency
  • ~0ms tool latency (knowledge is pre-vectorized)

TOOL Mode:
  • Executes deterministic API calls (stats, schedules)
  • Returns structured data from external sources
  • Requires context (player_id, season, etc.)

HYBRID Mode:
  • Combines both: tools for data, RAG for context
  • Enriches tool results with knowledge base info
  • Highest latency but most comprehensive answers
    """)


if __name__ == "__main__":
    asyncio.run(main())
