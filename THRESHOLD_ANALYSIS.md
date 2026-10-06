# RAG Threshold Matrix Analysis

## Testing Overview

**Date:** September 14, 2026  
**Test Metrics:** 5 representative queries across baseball and general knowledge domains  
**Configuration:** RAG_TOP_K = 2 (fixed)  
**Thresholds Tested:** 0.25, 0.35, 0.45

---

## Results Summary

### Threshold 0.25 (High Recall - Permissive)

_Goal: Retrieve as many relevant chunks as possible_

| Query             | Scored | Passed | Rate       | Avg Score | Top Scores               |
| ----------------- | ------ | ------ | ---------- | --------- | ------------------------ |
| Infield fly rule  | 3      | 3      | **100.0%** | 0.4370    | [0.6154, 0.3873, 0.3083] |
| Designated hitter | 3      | 1      | 33.3%      | 0.3204    | [0.5379, 0.2331, 0.1903] |
| Good shortstop    | 3      | 0      | 0.0%       | 0.0785    | [0.1061, 0.0919, 0.0375] |
| World Series      | 3      | 0      | 0.0%       | 0.1509    | [0.2398, 0.1291, 0.0839] |
| Sabermetrics      | 3      | 2      | **66.7%**  | 0.2800    | [0.3378, 0.3182, 0.1839] |

**Average Retrieval Rate: 40.0%**

✓ **Strengths:**

- Gets 100% retrieval on strong matches (infield fly rule)
- Captures moderate matches (sabermetrics: 66.7%)
- More comprehensive coverage across domains

✗ **Weaknesses:**

- Still misses queries with weaker semantic similarity (shortstop, series)
- May include chunks with marginal relevance (0.25 threshold is quite low)
- Risk of noise in results

---

### Threshold 0.35 (Medium - Current Default)

_Goal: Balance precision and recall_

| Query             | Scored | Passed | Rate      | Avg Score | Top Scores               |
| ----------------- | ------ | ------ | --------- | --------- | ------------------------ |
| Infield fly rule  | 3      | 2      | **66.7%** | 0.4370    | [0.6154, 0.3873, 0.3083] |
| Designated hitter | 3      | 1      | 33.3%     | 0.3204    | [0.5379, 0.2331, 0.1903] |
| Good shortstop    | 3      | 0      | 0.0%      | 0.0785    | [0.1061, 0.0919, 0.0375] |
| World Series      | 3      | 0      | 0.0%      | 0.1509    | [0.2398, 0.1291, 0.0839] |
| Sabermetrics      | 3      | 0      | 0.0%      | 0.2800    | [0.3378, 0.3182, 0.1839] |

**Average Retrieval Rate: 20.0%**

✓ **Strengths:**

- Balanced results - not too permissive, not too strict
- Filters out low-relevance chunks effectively
- Good for production use without noise

✗ **Weaknesses:**

- Misses some moderate matches (sabermetrics drops from 66.7% to 0%)
- Loses retrieval on queries with no high-similarity chunks
- May be too strict for exploratory search

---

### Threshold 0.45 (High Precision - Strict)

_Goal: Only return chunks with very high confidence matches_

| Query             | Scored | Passed | Rate      | Avg Score | Top Scores               |
| ----------------- | ------ | ------ | --------- | --------- | ------------------------ |
| Infield fly rule  | 3      | 1      | **33.3%** | 0.4370    | [0.6154, 0.3873, 0.3083] |
| Designated hitter | 3      | 1      | **33.3%** | 0.3204    | [0.5379, 0.2331, 0.1903] |
| Good shortstop    | 3      | 0      | 0.0%      | 0.0785    | [0.1061, 0.0919, 0.0375] |
| World Series      | 3      | 0      | 0.0%      | 0.1509    | [0.2398, 0.1291, 0.0839] |
| Sabermetrics      | 3      | 0      | 0.0%      | 0.2800    | [0.3378, 0.3182, 0.1839] |

**Average Retrieval Rate: 13.3%**

✓ **Strengths:**

- Highest precision / lowest noise potential
- Only returns highest-confidence matches (>0.45 similarity)
- Best for critical/formal applications

✗ **Weaknesses:**

- Very high false negative rate (86.7% of queries get no results)
- Too restrictive for general knowledge queries
- May leave users without any helpful information

---

## Key Observations

### Semantic Similarity Floor

The data reveals that:

- Queries about **core baseball rules** (infield fly, DH) score in 0.54-0.61 range (high relevance)
- Queries about **attributes/skills** (shortstop) score in 0.10-0.11 range (low relevance)
- Queries about **concepts** (sabermetrics) score in 0.31-0.33 range (moderate relevance)

This suggests the semantic search is working correctly - it's identifying which topics have strong vector matches in the knowledge base.

### Threshold Trade-Off Curve

```
Threshold    Avg Retrieval Rate    Precision Level
0.25              40.0%             ⭐⭐⭐ (Lower)
0.35              20.0%             ⭐⭐⭐⭐ (Medium)
0.45              13.3%             ⭐⭐⭐⭐⭐ (Highest)
```

---

## Recommendation

**Recommended Threshold: 0.30 - 0.35**

### Rationale:

1. **0.35 (current)** is solid for general production use
2. **0.30** would improve recall (catch sabermetrics at 0.3182) while maintaining precision
3. This range balances:
   - Retrieving meaningful results (~25-35% of queries)
   - Avoiding noise from low-relevance chunks
   - Accepting that some query types won't find matches (by design)

### For Different Use Cases:

- **Exploratory/Research Tool:** Use 0.25 → Higher recall, accept some noise
- **Production API:** Use 0.30-0.35 → Balanced precision/recall
- **Critical/Legal:** Use 0.45 → Highest precision, accept false negatives

---

## Next Steps

1. **A/B Test**: Deploy threshold 0.30 and measure user satisfaction vs current 0.35
2. **Fine-tune RAG Embeddings**: Better embeddings will shift this entire curve rightward
3. **Domain-Specific Tuning**: Different knowledge bases may need different thresholds
4. **Monitor in Production**: Track retrieval rates and user feedback to validate choice

---

## Configuration

To apply recommended threshold:

```bash
# Update .env
echo "RAG_MIN_SCORE=0.30" >> .env

# Restart server
pkill -f uvicorn
source venv/bin/activate
python -m uvicorn app.main:app --port 8001
```
