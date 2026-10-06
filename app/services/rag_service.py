"""Week 3 RAG service: ingestion, chunking, vector retrieval, and citations."""

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cachetools import TTLCache

from app.config import get_settings
from app.services.corpus_ingestion import CorpusValidator, APPROVED_SOURCES


TOKEN_REGEX = re.compile(r"[a-z0-9]+")


@dataclass
class CorpusChunk:
    """A single retrievable text chunk with source metadata."""

    chunk_id: str
    source_id: str
    title: str
    url: str
    section: str
    text: str
    vector: dict[str, float]


@dataclass
class RetrievalResult:
    """A retrieval hit with similarity score and source metadata."""

    chunk_id: str
    source_id: str
    title: str
    url: str
    section: str
    snippet: str
    score: float


class RAGService:
    """Local RAG service backed by markdown corpus and sparse-vector retrieval."""

    def __init__(self) -> None:
        settings = get_settings()
        self.top_k = settings.rag_top_k
        self.min_score = settings.rag_min_score
        self.chunk_size = settings.rag_chunk_size_chars
        self.chunk_overlap = settings.rag_chunk_overlap_chars
        self.corpus_dir = Path(__file__).resolve().parent.parent / "rag_corpus"
        self.query_cache: TTLCache = TTLCache(maxsize=300, ttl=180)
        self.validator = CorpusValidator()
        self._chunks: list[CorpusChunk] = []
        self._ingested = False

    def ensure_ingested(self) -> None:
        """Load and chunk corpus on first retrieval."""
        if self._ingested:
            return

        chunks: list[CorpusChunk] = []
        for file_path in sorted(self.corpus_dir.glob("*.md")):
            content = file_path.read_text(encoding="utf-8").strip()
            if not content:
                continue

            # Map filename to source_id (rules_infield_fly.md → rules_infield_fly)
            source_id = file_path.stem

            # Validate against corpus policy
            if source_id not in APPROVED_SOURCES:
                # Log warning but skip (in production, may want to fail)
                continue

            approved = APPROVED_SOURCES[source_id]
            title = approved["title"]
            url = approved["url"]
            attribution = approved["attribution"]

            for index, chunk_text in enumerate(self._chunk_text(content)):
                section = title
                chunk_id = f"{source_id}:{index}"
                chunks.append(
                    CorpusChunk(
                        chunk_id=chunk_id,
                        source_id=source_id,
                        title=title,
                        url=url,
                        section=section,
                        text=chunk_text,
                        vector=self._vectorize(chunk_text),
                    )
                )

        self._chunks = chunks
        self._ingested = True

    def retrieve(self, query: str, top_k: Optional[int] = None) -> list[RetrievalResult]:
        """Return top-k semantic matches for a query."""
        self.ensure_ingested()

        cache_key = f"{query.strip().lower()}::{top_k or self.top_k}"
        if cache_key in self.query_cache:
            return list(self.query_cache[cache_key])

        qvec = self._vectorize(query)
        limit = top_k or self.top_k
        scored: list[RetrievalResult] = []

        for chunk in self._chunks:
            score = self._cosine_similarity(qvec, chunk.vector)
            if score < self.min_score:
                continue
            scored.append(
                RetrievalResult(
                    chunk_id=chunk.chunk_id,
                    source_id=chunk.source_id,
                    title=chunk.title,
                    url=chunk.url,
                    section=chunk.section,
                    snippet=self._snippet(chunk.text),
                    score=score,
                )
            )

        scored.sort(key=lambda item: item.score, reverse=True)
        top_results = scored[:limit]
        self.query_cache[cache_key] = list(top_results)
        return top_results

    def retrieval_stats(self, results: list[RetrievalResult]) -> dict[str, int]:
        """Basic retrieval telemetry used in copilot warnings and tracing."""
        unique_sources = {item.source_id for item in results}
        return {
            "candidates": len(self._chunks),
            "selected": len(results),
            "source_diversity": len(unique_sources),
        }

    def _chunk_text(self, text: str) -> list[str]:
        """Chunk by character window with overlap for stable token budgets."""
        normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if len(normalized) <= self.chunk_size:
            return [normalized]

        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(start + self.chunk_size, len(normalized))
            chunks.append(normalized[start:end])
            if end == len(normalized):
                break
            start = max(0, end - self.chunk_overlap)
        return chunks

    def _extract_title(self, content: str, fallback: str) -> str:
        first_line = content.splitlines()[0].strip() if content else ""
        if first_line.startswith("#"):
            return first_line.lstrip("# ").strip() or fallback
        return fallback

    def _source_url_for(self, stem: str) -> str:
        if "infield_fly" in stem:
            return "https://www.mlb.com/glossary/rules/infield-fly-rule"
        if "designated_hitter" in stem:
            return "https://www.mlb.com/glossary/standard-stats/designated-hitter"
        return "https://www.mlb.com/history"

    def _vectorize(self, text: str) -> dict[str, float]:
        tokens = TOKEN_REGEX.findall(text.lower())
        if not tokens:
            return {}

        tf: dict[str, float] = {}
        for token in tokens:
            tf[token] = tf.get(token, 0.0) + 1.0

        norm = math.sqrt(sum(value * value for value in tf.values()))
        if norm == 0:
            return tf

        return {token: value / norm for token, value in tf.items()}

    def _cosine_similarity(self, left: dict[str, float], right: dict[str, float]) -> float:
        if not left or not right:
            return 0.0

        if len(left) > len(right):
            left, right = right, left

        dot = 0.0
        for token, weight in left.items():
            dot += weight * right.get(token, 0.0)
        return dot

    def _snippet(self, text: str, max_len: int = 200) -> str:
        compact = " ".join(text.split())
        return compact[:max_len]


_rag_service: Optional[RAGService] = None


def get_rag_service() -> RAGService:
    """Return singleton RAG service instance."""
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service
