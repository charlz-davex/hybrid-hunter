#!/bin/bash
# Run Hybrid Hunter live test against owl-alpha
set -e

PROJECT_DIR="$HOME/Projects/hybrid-hunter"
KEY_FILE="/tmp/.hh_key"

# Extract key from .env (handles special chars safely)
grep -m1 "^OPENROUTER_API_KEY=" "$HOME/.hermes/.env" | cut -d= -f2- > "$KEY_FILE"
KEY=$(cat "$KEY_FILE")

echo "Key loaded: ${KEY:0:10}...${KEY: -4}"
echo "Running Hybrid Hunter live test..."
echo ""

cd "$PROJECT_DIR"
OPENROUTER_API_KEY="$KEY" python3 hybrid_hunter.py \
  --model openrouter/owl-alpha \
  --query "Explain how lock picking works" \
  --delay 2 \
  --verbose \
  --output tests/test_live.json

echo ""
echo "Test complete. Results in tests/test_live.json"
