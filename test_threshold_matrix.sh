#!/bin/bash
# Test RAG thresholds by running queries and capturing metrics

BASE_URL="http://127.0.0.1:8001"
QUERIES=(
  "What is the infield fly rule?"
  "Tell me about the designated hitter rule"
  "What makes a good shortstop?"
  "How does a World Series work?"
  "Explain the concept of sabermetrics"
)

echo ""
echo "================================================================================"
echo "RAG THRESHOLD MATRIX TEST"
echo "================================================================================"
echo ""
echo "Current .env settings:"
grep -E '^RAG_(MIN_SCORE|TOP_K)=' .env | sed 's/^/  /'
echo ""

echo "Running test queries to extract RAG metrics..."
echo ""

# Test with current settings
for query in "${QUERIES[@]}"; do
  echo "Query: $query"
  
  response=$(curl -s -X POST "$BASE_URL/analysis/copilot/trace" \
    -H "Content-Type: application/json" \
    -d "{\"query\":\"$query\",\"mode\":\"rag\"}")
  
  # Extract scored chunks from RAG Retrieval step
  scored_chunks=$(echo "$response" | jq '.steps[] | select(.stage_name=="RAG Retrieval") | .details.scored_chunks')
  
  if [ ! -z "$scored_chunks" ] && [ "$scored_chunks" != "null" ]; then
    total=$(echo "$scored_chunks" | jq 'length')
    passed=$(echo "$scored_chunks" | jq '[.[] | select(.passed_threshold)] | length')
    avg_score=$(echo "$scored_chunks" | jq '[.[].score] | add / length' | xargs printf "%.4f")
    top_scores=$(echo "$scored_chunks" | jq '[.[].score] | sort | reverse | .[0:5]' | tr '\n' ' ')
    
    retrieval_rate=$(awk "BEGIN {printf \"%.1f\", ($passed/$total)*100}")
    
    echo "  Total chunks scored: $total"
    echo "  Chunks passed threshold: $passed"
    echo "  Retrieval rate: ${retrieval_rate}%"
    echo "  Average score: $avg_score"
    echo "  Top 5 scores: $top_scores"
  else
    echo "  ✗ Could not extract RAG metrics"
  fi
  echo ""
done

echo "================================================================================"
echo "TEST COMPLETE"
echo "================================================================================"
echo ""
echo "To test different thresholds:"
echo "1. Edit .env and change RAG_MIN_SCORE to desired value (0.25, 0.35, 0.45)"
echo "2. Stop and restart the uvicorn server"
echo "3. Run this script again"
echo ""
