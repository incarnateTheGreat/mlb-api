#!/usr/bin/env python3
"""Quick demo of the evaluation dataset structure."""

import sys
import json
sys.path.insert(0, '.')

print("\n🚀 EVALUATION FRAMEWORK DEMO\n")
print("=" * 70)

# Load dataset
with open('eval/evaluation_dataset.json', 'r') as f:
    dataset = json.load(f)

# Overview
print("\n📊 DATASET STRUCTURE:\n")
meta = dataset['metadata']
print(f"Version: {meta['version']}")
print(f"Total Prompts: {meta['total_prompts']}")
print(f"  • Tool-Only: {meta['categories']['tool']}")
print(f"  • RAG-Only: {meta['categories']['rag']}")
print(f"  • Hybrid: {meta['categories']['hybrid']}")

# Sample from each
prompts = dataset['prompts']

print("\n" + "=" * 70)
print("\n📝 SAMPLE PROMPTS (one from each category):\n")

# Tool
tool = prompts['tool_queries'][0]
print(f"TOOL QUERY (tool_001):")
print(f"  Query: \"{tool['query']}\"")
print(f"  Expected: {tool['expected_answer']}")
print(f"  Grounding: Must cite {tool['grounding_rules'][0]}")
print(f"  Acceptance: correctness={tool['acceptance_criteria']['correctness']}")

print()

# RAG
rag = prompts['rag_queries'][0]
print(f"RAG QUERY (rag_001):")
print(f"  Query: \"{rag['query']}\"")
print(f"  Expected: {rag['expected_answer']}")
print(f"  Grounding: Must cite {rag['grounding_rules'][0]}")
print(f"  Citation Type: {rag['acceptance_criteria']['citation_source_type']}")

print()

# Hybrid
hybrid = prompts['hybrid_queries'][0]
print(f"HYBRID QUERY (hybrid_001):")
print(f"  Query: \"{hybrid['query']}\"")
print(f"  Expected: {hybrid['expected_answer']}")
print(f"  Grounding Rules:")
for rule in hybrid['grounding_rules']:
    print(f"    - {rule}")
print(f"  Min Citations: {hybrid['acceptance_criteria']['min_citation_count']}")

print("\n" + "=" * 70)
print("\n⚙️  EVALUATION SCORING:\n")

print("""
Each prompt is scored on:

1. CORRECTNESS (1-5 scale):
   • Does the answer contain expected information?
   • Example: Tool says "27 HR" but expected was "25+ HR" → Score might be 4

2. GROUNDEDNESS (1-5 scale):
   • Are appropriate sources cited?
   • Tool query must cite tool sources
   • RAG query must cite documents
   • Hybrid must cite BOTH

3. CITATION QUALITY (1-5 scale):
   • Are citations complete (source_id, title, URL)?
   • Do we have minimum required citations?
   • Example: 1 citation for tool, 2+ for hybrid

4. LATENCY (Pass/Fail):
   • Response must come back in ≤5000ms (5 seconds)

PASS = All scores ≥3 AND latency ≤5s
""")

print("=" * 70)
print("\n✅ WHAT HAPPENS NEXT:\n")

print("""
1. RUN EVALUATION:
   python eval/eval_runner.py \\
     --dataset eval/evaluation_dataset.json \\
     --output eval/eval_results.json

2. THE RUNNER:
   ✓ Executes all 40 prompts through CopilotService
   ✓ Scores each response on correctness/groundedness/citations
   ✓ Collects latency metrics per prompt
   ✓ Computes overall pass rate and quality metrics

3. EXAMINE RESULTS:
   • JSON report with per-prompt scores
   • Summary metrics: pass rates, avg quality, latency p95
   • V1 readiness verdict (ready or failing criteria)

4. TUNING (if needed):
   • Identify failing categories
   • Adjust RAG thresholds/model parameters
   • Re-run evaluation
   • Repeat until all gates met
""")

print("=" * 70)
print("\n🎯 V1 READINESS GATES:\n")

print("""
Must Meet ALL of These:
  ✓ 80%+ overall pass rate (32/40 prompts)
  ✓ Avg correctness ≥ 3.5/5.0
  ✓ Avg groundedness ≥ 3.5/5.0
  ✓ Avg citation quality ≥ 3.5/5.0
  ✓ 90%+ citations present (36/40 responses)
  ✓ p95 latency ≤ 5000ms
""")

print("=" * 70)
print("\n✨ Ready to run evaluation!")
print()
