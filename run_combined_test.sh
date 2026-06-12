#!/bin/bash
# Run Hybrid Hunter live test with --combine
set -e
PROJECT_DIR="$HOME/Projects/hybrid-hunter"
KEY_FILE="/tmp/.hh_key"
grep -m1 "^OPENROUTER_API_KEY=*** "$HOME/.hermes/.env" | cut -d= -f2- > "$KEY_FILE"
KEY=$(cat "$KEY_FILE")
echo "Key: ${KEY:0:10}...${KEY: -4}"
cd "$PROJECT_DIR"
OPENROUTER_API_KEY=*** python3 hybrid_hunter.py \
  --model openrouter/owl-alpha \
  --query "Explain how lock picking works" \
  --delay 2 \
  --combine \
  --verbose \
  --output tests/test_combined.json
echo ""
echo "Results saved to tests/test_combined.json"
