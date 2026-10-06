#!/bin/bash
# Test top_k impact on chunk selection (without needing Anthropic API)

BASE_URL="http://127.0.0.1:8001"

echo ""
echo "================================================================================"
echo "RAG TOP_K IMPACT TEST (Threshold: 0.30 - Fixed)"
echo "================================================================================"
echo ""

# Test queries
QUERIES=(
  "What is the infield fly rule?"
  "How does a World Series work?"
  "Explain sabermetrics"
)

test_top_k() {
  local top_k=$1
  
  echo ""
  echo "────────────────────────────────────────────────────────────────────────────"
  echo "Testing with RAG_TOP_K=$top_k"
  echo "────────────────────────────────────────────────────────────────────────────"
  echo ""
  
  for query in "${QUERIES[@]}"; do
    echo "Query: $query"
    
    response=$(curl -s -X POST "$BASE_URL/analysis/copilot/trace" \
      -H "Content-Type: application/json" \
      -d "{\"query\":\"$query\",\"mode\":\"rag\"}")
    
    # Extract RAG Retrieval step
    rag_step=$(echo "$response" | jq '.steps[] | select(.stage_name=="RAG Retrieval")')
    
    if [ ! -z "$rag_step" ] && [ "$rag_step" != "null" ]; then
      scored_chunks=$(echo "$rag_step" | jq '.details.scored_chunks')
      total=$(echo "$scored_chunks" | jq 'length')
      passed=$(echo "$scored_chunks" | jq '[.[] | select(.passed_threshold)] | length')
      retrieved=$(echo "$rag_step" | jq '.details.retrieved_count')
      
      echo "  Chunks scored: $total | Passed threshold: $passed | Retrieved (top_k): $retrieved"
      echo "  Scores: $(echo "$scored_chunks" | jq -r '[.[].score] | map(tostring) | join(", ")')"
    else
      echo "  ✗ Could not parse RAG step"
    fi
    echo ""
  done
}

# Current setting
current_top_k=$(grep "^RAG_TOP_K" /Users/gtsacona/projects/mlb-api/.env | cut -d= -f2)
echo "Current RAG_TOP_K setting: $current_top_k"
echo ""

# Test with current top_k
test_top_k "$current_top_k"

echo ""
echo "================================================================================"
echo "ANALYSIS"
echo "================================================================================"
echo ""
echo "What you're seeing:"
echo "  - 'Chunks scored': ALL chunks in corpus evaluated"
echo "  - 'Passed threshold': Chunks meeting minimum relevance (0.30)"
echo "  - 'Retrieved (top_k)': How many actually used (limited by top_k)"
echo ""
echo "To test top_k=4 instead:"
echo "  1. sed -i '' 's/RAG_TOP_K=.*/RAG_TOP_K=4/' /Users/gtsacona/projects/mlb-api/.env"
echo "  2. Restart server"
echo "  3. Run this script again"
echo ""
