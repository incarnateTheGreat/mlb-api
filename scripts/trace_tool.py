#!/usr/bin/env python
"""Quick trace of tool vs RAG query flow."""

import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.models.analysis import CopilotRequest
from app.services.copilot_service import CopilotService


async def main():
    service = CopilotService()
    
    print("="*70)
    print("TOOL QUERY TRACE (with context)")
    print("="*70)
    
    request = CopilotRequest(
        query="What are the player stats?",
        mode="tool",
        context={"player_id": 545361, "season": 2024},
    )
    
    print(f"Query: {request.query}")
    print(f"Context: {request.context}")
    print()
    
    # Show tool selection
    print("Tool Selection:")
    selected = service._select_tool(request)
    if selected:
        tool_name, tool_inputs = selected
        print(f"  ✓ Selected: {tool_name}")
        print(f"  ✓ Inputs: {tool_inputs}")
    else:
        print(f"  ✗ No tool selected")
    print()
    
    # Run query
    print("Executing query...")
    response = await service.query(request)
    
    print(f"\nAnswer: {response.answer}")
    print(f"Confidence: {response.confidence}")
    print(f"Citations: {len(response.citations)} ({[c.source_type for c in response.citations]})")
    print(f"Tools used: {[t.tool_name for t in response.tools_used]}")
    print(f"Latency: {response.timing.total_ms}ms (tool: {response.timing.tools_ms}ms, model: {response.timing.model_ms}ms)")
    
    if response.warnings:
        print(f"Warnings: {response.warnings}")


if __name__ == "__main__":
    asyncio.run(main())
