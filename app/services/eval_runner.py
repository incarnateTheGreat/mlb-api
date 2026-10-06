"""
Eval runner: Execute test cases and compute grounding/citation/latency metrics.
"""

import json
import time
from dataclasses import dataclass, asdict
from typing import Optional

from app.eval_dataset import EvalCase, get_eval_cases
from app.models.analysis import CopilotRequest, CopilotResponse
from app.services.copilot_service import get_copilot_service


@dataclass
class CaseScore:
    """Score for a single eval case."""
    case_id: str
    case_description: str
    passed: bool
    mode: str
    query: str
    answer_length: int
    confidence: str
    latency_ms: int
    citation_count: int
    citation_types: list[str]
    citations_correct: bool
    answer_quality: bool
    has_warnings: bool
    expected_warnings: bool
    failures: list[str]
    score: float


class EvalRunner:
    """Run eval cases and produce metrics."""

    def __init__(self):
        self.copilot_service = get_copilot_service()
        self.results: list[CaseScore] = []

    async def run_all(self, mode: Optional[str] = None) -> list[CaseScore]:
        """
        Run all eval cases, optionally filtered by mode.
        
        Args:
            mode: Filter by 'tool', 'rag', 'hybrid', or None for all
        
        Returns:
            List of scored cases
        """
        cases = get_eval_cases(mode)
        self.results = []

        for case in cases:
            score = await self.run_case(case)
            self.results.append(score)

        return self.results

    async def run_case(self, case: EvalCase) -> CaseScore:
        """
        Run a single eval case and score it.
        
        Args:
            case: EvalCase to evaluate
        
        Returns:
            CaseScore with pass/fail and detailed metrics
        """
        request = CopilotRequest(
            query=case.query,
            context=case.context,
            mode=case.mode,
            max_tokens=1024,
            temperature=0.7,
        )

        response = await self.copilot_service.query(request)
        failures = []
        score_value = 0.0

        # Check orchestration mode
        if response.query_mode != case.mode:
            failures.append(f"Mode mismatch: expected {case.mode}, got {response.query_mode}")

        # Check confidence
        conf_rank = {"low": 0, "medium": 1, "high": 2}
        if conf_rank.get(response.confidence, -1) < conf_rank.get(case.min_confidence, -1):
            failures.append(
                f"Confidence too low: expected min {case.min_confidence}, got {response.confidence}"
            )

        # Check latency
        if response.timing.total_ms > case.max_latency_ms:
            failures.append(
                f"Latency exceeded: expected max {case.max_latency_ms}ms, got {response.timing.total_ms}ms"
            )

        # Check citations
        actual_citation_types = {c.source_type for c in response.citations}
        expected_types_set = set(case.expected_citations_types)
        citations_correct = actual_citation_types == expected_types_set
        if not citations_correct and case.expected_citations_types:
            failures.append(
                f"Citation types mismatch: expected {expected_types_set}, got {actual_citation_types}"
            )

        # Check answer quality (keyword presence)
        answer_lower = response.answer.lower()
        missing_keywords = [
            kw for kw in case.expected_answer_contains
            if kw.lower() not in answer_lower
        ]
        answer_quality = len(missing_keywords) == 0
        if missing_keywords:
            failures.append(f"Answer missing keywords: {missing_keywords}")

        # Check warnings
        has_warnings = len(response.warnings) > 0
        warning_check = has_warnings == case.should_warn
        if not warning_check:
            failures.append(
                f"Warning mismatch: expected {case.should_warn}, got {has_warnings}"
            )

        # Compute score (0.0 to 1.0)
        max_checks = 6
        passed_checks = 0
        if response.query_mode == case.mode:
            passed_checks += 1
        if conf_rank.get(response.confidence, -1) >= conf_rank.get(case.min_confidence, -1):
            passed_checks += 1
        if response.timing.total_ms <= case.max_latency_ms:
            passed_checks += 1
        if citations_correct:
            passed_checks += 1
        if answer_quality:
            passed_checks += 1
        if warning_check:
            passed_checks += 1
        score_value = passed_checks / max_checks

        passed = len(failures) == 0

        return CaseScore(
            case_id=case.case_id,
            case_description=case.description,
            passed=passed,
            mode=case.mode,
            query=case.query,
            answer_length=len(response.answer),
            confidence=response.confidence,
            latency_ms=response.timing.total_ms,
            citation_count=len(response.citations),
            citation_types=list(actual_citation_types),
            citations_correct=citations_correct,
            answer_quality=answer_quality,
            has_warnings=has_warnings,
            expected_warnings=case.should_warn,
            failures=failures,
            score=score_value,
        )

    def generate_report(self) -> dict:
        """
        Generate aggregated metrics report.
        
        Returns:
            Report dict with overall stats and per-case details
        """
        if not self.results:
            return {"error": "No results to report"}

        total = len(self.results)
        passed_count = sum(1 for r in self.results if r.passed)
        pass_rate = passed_count / total if total > 0 else 0.0
        avg_score = sum(r.score for r in self.results) / total if total > 0 else 0.0
        avg_latency = sum(r.latency_ms for r in self.results) / total if total > 0 else 0.0

        by_mode = {}
        for mode in ["tool", "rag", "hybrid"]:
            mode_results = [r for r in self.results if r.mode == mode]
            if mode_results:
                by_mode[mode] = {
                    "count": len(mode_results),
                    "passed": sum(1 for r in mode_results if r.passed),
                    "pass_rate": sum(1 for r in mode_results if r.passed) / len(mode_results),
                    "avg_score": sum(r.score for r in mode_results) / len(mode_results),
                    "avg_latency_ms": sum(r.latency_ms for r in mode_results) / len(mode_results),
                }

        return {
            "summary": {
                "total_cases": total,
                "passed": passed_count,
                "pass_rate": pass_rate,
                "avg_score": avg_score,
                "avg_latency_ms": avg_latency,
            },
            "by_mode": by_mode,
            "cases": [asdict(r) for r in self.results],
        }

    def save_report(self, filepath: str) -> None:
        """Save report to JSON file."""
        report = self.generate_report()
        with open(filepath, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report saved to {filepath}")
