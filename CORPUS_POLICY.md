# RAG Corpus Policy

## Overview

This document defines the source whitelist, metadata contract, and governance for the MLB API RAG knowledge base. All ingested sources must comply with this policy.

## Approved Sources

### Category 1: Official MLB Rules & Regulations

- **MLB Official Rules** (rulebook sections)
  - URL pattern: `https://www.mlb.com/official-information/rules`
  - License: Public domain (U.S. government regulations)
  - Attribution: "MLB Official Rules"

### Category 2: Public Statistical Data

- **MLB StatsAPI Public Data**
  - URL pattern: `https://statsapi.mlb.com/api/v1`
  - License: Public data endpoint
  - Attribution: "MLB StatsAPI"

### Category 3: Historical Baseball Knowledge

- **Baseball Reference (public sections)**
  - URL pattern: `https://www.baseball-reference.com/`
  - License: Community contribution, freely accessible
  - Attribution: "Baseball Reference"

## Metadata Contract

Every ingested source must include:

```python
{
    "source_id": "rules_infield_fly",           # Unique identifier (kebab-case)
    "title": "Infield Fly Rule",                # Human-readable title
    "url": "https://...",                       # Source URL (HTTPS)
    "section": "Rule 5.09",                     # Section/heading within source
    "license_type": "public_domain",            # public_domain, cc_by, public_data
    "attribution": "MLB Official Rules",        # Attribution text
    "ingested_date": "2026-09-15",              # ISO 8601 date
    "version": "2024",                          # Source version (if applicable)
}
```

## Non-Whitelisted Categories

❌ **Prohibited** (do not ingest):

- Paywalled content (ESPN+, MLB.TV subscriber-only)
- User-generated content (Reddit, fan blogs)
- Copyrighted commentary or analysis
- Real-time data requiring license agreements
- Personal player opinions or quotes (without explicit permission)
- Predictive models or proprietary algorithms

## Ingestion Process

1. **Source Validation**
   - Verify source is in approved list
   - Check license compliance
   - Confirm HTTPS availability

2. **Metadata Attachment**
   - Embed metadata in markdown frontmatter (YAML)
   - Validate all required fields present
   - Record ingestion timestamp

3. **Chunking & Embedding**
   - Split into semantic chunks (700 char default)
   - Generate vector embeddings
   - Maintain source-to-chunk mapping

4. **Quality Checks**
   - Verify chunks are retrievable
   - Test citation building (URLs resolve, snippets are accurate)
   - Log chunk statistics

## Citation Requirements

All RAG responses must include:

- ✓ **source_id**: Links back to corpus metadata
- ✓ **title**: Human-readable source name
- ✓ **url**: Clickable link to original source
- ✓ **snippet**: Relevant excerpt (50-200 chars)
- ✓ **source_type**: Always "document" for RAG sources

Example citation:

```json
{
  "source_id": "rules_infield_fly",
  "source_type": "document",
  "title": "Infield Fly Rule",
  "url": "https://www.mlb.com/official-information/rules",
  "snippet": "An infield fly is a fair fly ball that an infielder can catch with ordinary effort.",
  "section": "Rule 5.09(a)(5)"
}
```

## Versioning

- **Current Policy Version**: 1.0
- **Effective Date**: September 15, 2026
- **Last Updated**: September 15, 2026

Future major versions will be documented in git commit history with breaking change notes.

## Governance

- Policy changes require approval from the AI engineering team
- Source additions require explicit whitelist modification
- Quarterly audit of ingested sources against this policy
- Any compliance violations result in immediate source removal
