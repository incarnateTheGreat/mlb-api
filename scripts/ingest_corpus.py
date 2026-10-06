#!/usr/bin/env python3
"""
Corpus ingestion management script.

Usage:
    python scripts/ingest_corpus.py --validate    # Check corpus compliance
    python scripts/ingest_corpus.py --stats       # Print ingestion statistics
    python scripts/ingest_corpus.py --add <source_id>  # Add new source
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.corpus_ingestion import CorpusValidator, CorpusIngestionStats, APPROVED_SOURCES, estimate_token_count
from app.services.rag_service import get_rag_service


def validate_corpus():
    """Validate all sources in the corpus."""
    print("\n" + "="*70)
    print("CORPUS VALIDATION")
    print("="*70 + "\n")

    corpus_dir = Path(__file__).parent.parent / "app" / "rag_corpus"
    validator = CorpusValidator()

    print(f"Corpus directory: {corpus_dir}")
    print(f"Approved sources: {len(APPROVED_SOURCES)}\n")

    # List approved sources
    print("Approved sources:")
    for source_id, metadata in sorted(APPROVED_SOURCES.items()):
        file_path = corpus_dir / f"{source_id}.md"
        exists = "✓" if file_path.exists() else "✗"
        print(f"  {exists} {source_id}")
        print(f"     Title: {metadata['title']}")
        print(f"     URL: {metadata['url']}")
        print(f"     Category: {metadata['category']}\n")

    # Check for unapproved files
    print("\nFiles in corpus directory:")
    unapproved = []
    for file_path in sorted(corpus_dir.glob("*.md")):
        source_id = file_path.stem
        if source_id in APPROVED_SOURCES:
            print(f"  ✓ {file_path.name}")
        else:
            print(f"  ! {file_path.name} (NOT IN WHITELIST)")
            unapproved.append(source_id)

    if unapproved:
        print(f"\n⚠️  WARNING: {len(unapproved)} unapproved source(s) in corpus!")
        return False

    print("\n✓ All corpus files comply with policy")
    return True


def ingest_and_report():
    """Ingest corpus and report statistics."""
    print("\n" + "="*70)
    print("CORPUS INGESTION REPORT")
    print("="*70 + "\n")

    stats = CorpusIngestionStats()
    stats.ingestion_start = datetime.utcnow()

    try:
        # Use RAG service to ingest (this validates and chunks)
        rag_service = get_rag_service()
        rag_service.ensure_ingested()

        # Collect stats
        stats.sources_processed = len(APPROVED_SOURCES)
        stats.sources_ingested = len([s for s in APPROVED_SOURCES.keys() 
                                       if (Path(__file__).parent.parent / "app" / "rag_corpus" / f"{s}.md").exists()])

        for chunk in rag_service._chunks:
            stats.total_chunks += 1
            stats.total_tokens += estimate_token_count(chunk.text)

        stats.ingestion_end = datetime.utcnow()

        # Print summary
        stats.print_summary()

        # Print chunk breakdown by source
        print("Chunks by source:")
        sources_seen = set()
        for chunk in rag_service._chunks:
            if chunk.source_id not in sources_seen:
                sources_seen.add(chunk.source_id)
                source_chunks = [c for c in rag_service._chunks if c.source_id == chunk.source_id]
                source_tokens = sum(estimate_token_count(c.text) for c in source_chunks)
                print(f"  {chunk.source_id}: {len(source_chunks)} chunks, ~{source_tokens} tokens")

        # Export stats
        stats_file = Path(__file__).parent.parent / "corpus_ingestion_stats.json"
        with open(stats_file, "w") as f:
            f.write(stats.to_json())
        print(f"\n✓ Stats exported to {stats_file}")

        return True

    except Exception as e:
        print(f"✗ Ingestion failed: {e}")
        return False


def list_sources():
    """List all approved sources."""
    print("\n" + "="*70)
    print("APPROVED CORPUS SOURCES")
    print("="*70 + "\n")

    for source_id, metadata in sorted(APPROVED_SOURCES.items()):
        print(f"ID: {source_id}")
        print(f"  Title:      {metadata['title']}")
        print(f"  Category:   {metadata['category']}")
        print(f"  License:    {metadata['license_type']}")
        print(f"  Attribution: {metadata['attribution']}")
        print(f"  URL:        {metadata['url']}\n")


def main():
    parser = argparse.ArgumentParser(description="Corpus ingestion management")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate corpus compliance with policy",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Ingest corpus and print statistics",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all approved sources",
    )

    args = parser.parse_args()

    if args.validate:
        success = validate_corpus()
        sys.exit(0 if success else 1)

    elif args.stats:
        success = ingest_and_report()
        sys.exit(0 if success else 1)

    elif args.list:
        list_sources()
        sys.exit(0)

    else:
        # Default: validate and report
        print(
            f"Usage: {parser.prog} [--validate] [--stats] [--list]\n"
            f"  --validate   Check corpus compliance\n"
            f"  --stats      Ingest and report statistics\n"
            f"  --list       List approved sources\n"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
