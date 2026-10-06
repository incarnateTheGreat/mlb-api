"""
Evaluation runner for Copilot v1 readiness.

Runs a batch of evaluation prompts against the copilot endpoint,
scores responses on correctness, groundedness, and citation quality,
and produces a summary report.

Usage:
    python eval/eval_runner.py --dataset eval/evaluation_dataset.json --output eval_results.json
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, asdict

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.copilot_service import CopilotService
from app.models.analysis import CopilotRequest


@dataclass
class EvalScore:
    """Score for a single evaluation prompt."""
    prompt_id: str
    query: str
    category: str  # tool, rag, hybrid
    
    # Timing
    latency_ms: int
    
    # Correctness (1-5 scale)
    correctness_score: int
    correctness_reason: str
    
    # Groundedness (1-5 scale)
    groundedness_score: int
    groundedness_reason: str
    
    # Citation Quality (1-5 scale)
    citation_quality_score: int
    citation_quality_reason: str
    
    # Overall
    pass_: bool  # meets baseline thresholds
    feedback: str
    
    # Metadata
    tool_count: int
    citation_count: int
    warnings_count: int


@dataclass
class EvalReport:
    """Overall evaluation report."""
    timestamp: str
    dataset_version: str
    total_prompts: int
    
    # Breakdown by category
    tool_prompts_count: int
    rag_prompts_count: int
    hybrid_prompts_count: int
    
    # Pass rates
    tool_pass_rate: float
    rag_pass_rate: float
    hybrid_pass_rate: float
    overall_pass_rate: float
    
    # Metrics
    avg_latency_ms: float
    p95_latency_ms: float
    avg_correctness: float
    avg_groundedness: float
    avg_citation_quality: float
    
    # Citation metrics
    avg_citation_count: float
    citation_present_rate: float
    
    # Status
    v1_ready: bool  # meets all thresholds
    v1_failing_criteria: list[str]
    
    scores: list[dict[str, Any]]


class EvaluationRunner:
    """Runs copilot evaluation against a dataset."""
    
    # Thresholds for v1 readiness
    MIN_PASS_RATE = 0.80  # 80%
    MIN_CORRECTNESS = 3.5  # Out of 5
    MIN_GROUNDEDNESS = 3.5
    MIN_CITATION_QUALITY = 3.5
    MIN_CITATION_PRESENT_RATE = 0.90  # 90%
    MAX_P95_LATENCY_MS = 5000  # 5 seconds
    
    def __init__(self, copilot_service: CopilotService) -> None:
        self.copilot_service = copilot_service
    
    async def run_evaluation(
        self,
        dataset_path: str,
        output_path: Optional[str] = None,
    ) -> EvalReport:
        """
        Run evaluation against a dataset.
        
        Args:
            dataset_path: Path to evaluation_dataset.json
            output_path: Optional output path for results JSON
        
        Returns:
            EvalReport with scores and metrics
        """
        # Load dataset
        with open(dataset_path, 'r') as f:
            dataset = json.load(f)
        
        metadata = dataset['metadata']
        prompts = dataset['prompts']
        
        # Collect all prompts with category
        all_prompts = []
        for tool_q in prompts['tool_queries']:
            all_prompts.append(('tool', tool_q))
        for rag_q in prompts['rag_queries']:
            all_prompts.append(('rag', rag_q))
        for hybrid_q in prompts['hybrid_queries']:
            all_prompts.append(('hybrid', hybrid_q))
        
        print(f"🚀 Running {len(all_prompts)} evaluation prompts...")
        
        # Run each prompt and collect scores
        scores = []
        latencies = []
        start_time = time.time()
        
        for idx, (category, prompt_data) in enumerate(all_prompts, 1):
            try:
                score = await self._evaluate_prompt(category, prompt_data)
                scores.append(score)
                latencies.append(score.latency_ms)
                
                status = "✅ PASS" if score.pass_ else "❌ FAIL"
                print(f"  [{idx}/{len(all_prompts)}] {score.prompt_id}: {status} ({score.latency_ms}ms)")
            except Exception as e:
                print(f"  [{idx}/{len(all_prompts)}] {prompt_data['id']}: 💥 ERROR - {str(e)}")
        
        elapsed = time.time() - start_time
        print(f"\n⏱️  Completed in {elapsed:.1f}s\n")
        
        # Compute report
        report = self._compute_report(metadata, scores, latencies)
        
        # Save if output path provided
        if output_path:
            with open(output_path, 'w') as f:
                json.dump(asdict(report), f, indent=2)
            print(f"📊 Results saved to {output_path}")
        
        return report
    
    async def _evaluate_prompt(
        self,
        category: str,
        prompt_data: dict[str, Any],
    ) -> EvalScore:
        """Evaluate a single prompt."""
        prompt_id = prompt_data['id']
        query = prompt_data['query']
        context = prompt_data.get('context', {})
        expected_answer = prompt_data.get('expected_answer', '')
        grounding_rules = prompt_data.get('grounding_rules', [])
        acceptance = prompt_data.get('acceptance_criteria', {})
        
        # Create copilot request
        request = CopilotRequest(
            query=query,
            mode="auto",
            context=context,
            max_tokens=1000,
            temperature=0.7,
        )
        
        # Execute
        start = time.time()
        response = await self.copilot_service.query(request)
        latency_ms = int((time.time() - start) * 1000)
        
        # Score response
        correctness_score, correctness_reason = self._score_correctness(
            category, query, response.answer, expected_answer, acceptance
        )
        
        groundedness_score, groundedness_reason = self._score_groundedness(
            response.answer, response.citations, grounding_rules, category
        )
        
        citation_quality_score, citation_quality_reason = self._score_citations(
            response.citations, grounding_rules, category
        )
        
        # Compute overall pass
        pass_ = (
            correctness_score >= 3
            and groundedness_score >= 3
            and citation_quality_score >= 3
            and latency_ms <= 5000
        )
        
        feedback = self._build_feedback(
            correctness_score, groundedness_score, citation_quality_score, latency_ms
        )
        
        return EvalScore(
            prompt_id=prompt_id,
            query=query,
            category=category,
            latency_ms=latency_ms,
            correctness_score=correctness_score,
            correctness_reason=correctness_reason,
            groundedness_score=groundedness_score,
            groundedness_reason=groundedness_reason,
            citation_quality_score=citation_quality_score,
            citation_quality_reason=citation_quality_reason,
            pass_=pass_,
            feedback=feedback,
            tool_count=len(response.tools_used),
            citation_count=len(response.citations),
            warnings_count=len(response.warnings),
        )
    
    def _score_correctness(
        self,
        category: str,
        query: str,
        answer: str,
        expected: str,
        acceptance: dict[str, Any],
    ) -> tuple[int, str]:
        """
        Score correctness (1-5).
        
        This is a heuristic scorer. In production, you'd want human evaluation
        or a more sophisticated LLM-based grading approach.
        """
        if not answer:
            return (1, "Empty response")
        
        answer_lower = answer.lower()
        expected_lower = expected.lower()
        
        # Check if key phrases match
        if any(phrase in answer_lower for phrase in expected_lower.split()):
            score = 4
            reason = "Answer contains expected key information"
        else:
            score = 2
            reason = "Answer may not contain expected information (heuristic check)"
        
        # Bonus if answer is substantive (>50 chars)
        if len(answer) > 50:
            score = min(5, score + 1)
            reason += "; substantive response"
        
        return (min(5, max(1, score)), reason)
    
    def _score_groundedness(
        self,
        answer: str,
        citations: list,
        grounding_rules: list[str],
        category: str,
    ) -> tuple[int, str]:
        """Score groundedness (1-5)."""
        if not citations:
            if category == "tool":
                return (1, "No citations for tool-based query")
            elif category == "rag":
                return (1, "No document citations for RAG query")
            else:
                return (2, "Hybrid query with no citations (partial grounding)")
        
        # Check citation types match category
        citation_types = [c.get('source_type') for c in citations]
        
        if category == "tool" and "tool" in citation_types:
            return (5, "Well-grounded with tool citations")
        elif category == "rag" and "document" in citation_types:
            return (5, "Well-grounded with document citations")
        elif category == "hybrid" and len(set(citation_types)) > 1:
            return (5, "Well-grounded with mixed citations")
        
        return (3, f"Citations present but may not match category: {citation_types}")
    
    def _score_citations(
        self,
        citations: list,
        grounding_rules: list[str],
        category: str,
    ) -> tuple[int, str]:
        """Score citation quality (1-5)."""
        if not citations:
            return (1, "No citations provided")
        
        required_count = 1
        if category == "hybrid":
            required_count = 2  # Hybrid should cite both tool and doc
        
        # Check citation completeness
        for citation in citations:
            if not citation.get('source_id'):
                return (2, "Citations missing source_id")
            if not citation.get('title'):
                return (2, "Citations missing title")
        
        # Score based on count and types
        if len(citations) < required_count:
            return (2, f"Insufficient citations ({len(citations)} vs {required_count} required)")
        
        if len(citations) >= required_count and all(
            c.get('source_id') and c.get('title') for c in citations
        ):
            return (5, f"Complete citations ({len(citations)})")
        
        return (3, "Citations present but incomplete")
    
    def _build_feedback(
        self,
        correctness: int,
        groundedness: int,
        citation: int,
        latency_ms: int,
    ) -> str:
        """Build human-readable feedback."""
        issues = []
        if correctness < 3:
            issues.append("correctness")
        if groundedness < 3:
            issues.append("groundedness")
        if citation < 3:
            issues.append("citations")
        if latency_ms > 5000:
            issues.append(f"latency ({latency_ms}ms)")
        
        if not issues:
            return "✅ Meets all thresholds"
        return f"⚠️ Issues: {', '.join(issues)}"
    
    def _compute_report(
        self,
        metadata: dict,
        scores: list[EvalScore],
        latencies: list[int],
    ) -> EvalReport:
        """Compute overall report metrics."""
        tool_scores = [s for s in scores if s.category == 'tool']
        rag_scores = [s for s in scores if s.category == 'rag']
        hybrid_scores = [s for s in scores if s.category == 'hybrid']
        
        # Pass rates
        tool_pass = sum(1 for s in tool_scores if s.pass_) / len(tool_scores) if tool_scores else 0.0
        rag_pass = sum(1 for s in rag_scores if s.pass_) / len(rag_scores) if rag_scores else 0.0
        hybrid_pass = sum(1 for s in hybrid_scores if s.pass_) / len(hybrid_scores) if hybrid_scores else 0.0
        overall_pass = sum(1 for s in scores if s.pass_) / len(scores) if scores else 0.0
        
        # Latency
        latencies_sorted = sorted(latencies) if latencies else [0]
        p95_idx = int(len(latencies_sorted) * 0.95)
        p95_latency = latencies_sorted[p95_idx] if latencies_sorted else 0
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        
        # Quality metrics
        avg_correctness = sum(s.correctness_score for s in scores) / len(scores) if scores else 0
        avg_groundedness = sum(s.groundedness_score for s in scores) / len(scores) if scores else 0
        avg_citation = sum(s.citation_quality_score for s in scores) / len(scores) if scores else 0
        
        # Citation presence
        citation_present = sum(1 for s in scores if s.citation_count > 0) / len(scores) if scores else 0.0
        avg_citation_count = sum(s.citation_count for s in scores) / len(scores) if scores else 0
        
        # v1 readiness check
        v1_ready = True
        v1_failing = []
        
        if overall_pass < self.MIN_PASS_RATE:
            v1_ready = False
            v1_failing.append(f"Overall pass rate {overall_pass:.1%} < {self.MIN_PASS_RATE:.1%}")
        
        if avg_correctness < self.MIN_CORRECTNESS:
            v1_ready = False
            v1_failing.append(f"Avg correctness {avg_correctness:.1f} < {self.MIN_CORRECTNESS}")
        
        if avg_groundedness < self.MIN_GROUNDEDNESS:
            v1_ready = False
            v1_failing.append(f"Avg groundedness {avg_groundedness:.1f} < {self.MIN_GROUNDEDNESS}")
        
        if avg_citation < self.MIN_CITATION_QUALITY:
            v1_ready = False
            v1_failing.append(f"Avg citation quality {avg_citation:.1f} < {self.MIN_CITATION_QUALITY}")
        
        if citation_present < self.MIN_CITATION_PRESENT_RATE:
            v1_ready = False
            v1_failing.append(f"Citation presence {citation_present:.1%} < {self.MIN_CITATION_PRESENT_RATE:.1%}")
        
        if p95_latency > self.MAX_P95_LATENCY_MS:
            v1_ready = False
            v1_failing.append(f"p95 latency {p95_latency}ms > {self.MAX_P95_LATENCY_MS}ms")
        
        return EvalReport(
            timestamp=datetime.utcnow().isoformat(),
            dataset_version=metadata.get('version', 'unknown'),
            total_prompts=len(scores),
            tool_prompts_count=len(tool_scores),
            rag_prompts_count=len(rag_scores),
            hybrid_prompts_count=len(hybrid_scores),
            tool_pass_rate=tool_pass,
            rag_pass_rate=rag_pass,
            hybrid_pass_rate=hybrid_pass,
            overall_pass_rate=overall_pass,
            avg_latency_ms=avg_latency,
            p95_latency_ms=p95_latency,
            avg_correctness=avg_correctness,
            avg_groundedness=avg_groundedness,
            avg_citation_quality=avg_citation,
            avg_citation_count=avg_citation_count,
            citation_present_rate=citation_present,
            v1_ready=v1_ready,
            v1_failing_criteria=v1_failing,
            scores=[asdict(s) for s in scores],
        )


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Copilot evaluation runner")
    parser.add_argument("--dataset", default="eval/evaluation_dataset.json")
    parser.add_argument("--output", default="eval/eval_results.json")
    args = parser.parse_args()
    
    # Initialize copilot service
    copilot_service = CopilotService()
    
    # Run evaluation
    runner = EvaluationRunner(copilot_service)
    report = await runner.run_evaluation(args.dataset, args.output)
    
    # Print summary
    print("\n" + "="*70)
    print("EVALUATION REPORT")
    print("="*70)
    print(f"Timestamp: {report.timestamp}")
    print(f"Total Prompts: {report.total_prompts} (Tool: {report.tool_prompts_count}, RAG: {report.rag_prompts_count}, Hybrid: {report.hybrid_prompts_count})")
    print()
    print("PASS RATES:")
    print(f"  Tool:    {report.tool_pass_rate:.1%}")
    print(f"  RAG:     {report.rag_pass_rate:.1%}")
    print(f"  Hybrid:  {report.hybrid_pass_rate:.1%}")
    print(f"  Overall: {report.overall_pass_rate:.1%}")
    print()
    print("QUALITY METRICS:")
    print(f"  Avg Correctness:        {report.avg_correctness:.2f}/5.0")
    print(f"  Avg Groundedness:       {report.avg_groundedness:.2f}/5.0")
    print(f"  Avg Citation Quality:   {report.avg_citation_quality:.2f}/5.0")
    print()
    print("CITATIONS:")
    print(f"  Presence Rate:          {report.citation_present_rate:.1%}")
    print(f"  Avg Count:              {report.avg_citation_count:.1f}")
    print()
    print("LATENCY:")
    print(f"  Average:                {report.avg_latency_ms:.0f}ms")
    print(f"  p95:                    {report.p95_latency_ms:.0f}ms")
    print()
    print("V1 READINESS:")
    if report.v1_ready:
        print("  ✅ READY FOR PRODUCTION")
    else:
        print("  ❌ NOT READY (failing criteria below):")
        for criteria in report.v1_failing_criteria:
            print(f"    - {criteria}")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(main())
