#!/bin/bash
set -e

echo "--- Starting Docker Smoke Test ---"

# 1. Wait for services to be healthy
echo "Waiting for API to be healthy..."
until [ "$(docker inspect --format='{{.State.Health.Status}}' ai-agentic-harness-lab-api-1)" == "healthy" ]; do
    printf "."
    sleep 2
done
echo " API is healthy!"

echo "Waiting for Worker to be healthy..."
until [ "$(docker inspect --format='{{.State.Health.Status}}' ai-agentic-harness-lab-worker-1)" == "healthy" ]; do
    printf "."
    sleep 2
done
echo " Worker is healthy!"

# 2. Trigger a dry-run run via API
echo "Creating a dry-run test run..."
RUN_ID="tetris-v1-SMOKE-$(date +%s)"
curl -s -X POST http://localhost:8008/api/runs \
  -H "Content-Type: application/json" \
  -d "{
    \"case_id\": \"tetris-v1\",
    \"run_id\": \"$RUN_ID\",
    \"prompt_id\": \"incremental\",
    \"executor_model\": \"gateway/dry-run\",
    \"max_steps\": 1,
    \"dry_run\": true
  }" | grep -q "\"run_id\":\"$RUN_ID\""

echo "Run $RUN_ID created successfully."

# 3. Wait for worker to pick it up and complete it
echo "Waiting for run to complete (should reach awaiting_manual_score since it is a dry-run)..."
MAX_ATTEMPTS=10
ATTEMPT=0
STATUS="pending"
until [ "$STATUS" == "awaiting_manual_score" ] || [ $ATTEMPT -ge $MAX_ATTEMPTS ]; do
    STATUS=$(curl -s http://localhost:8008/api/runs/$RUN_ID | grep -o '"status":"[^"]*"' | cut -d'"' -f4)
    echo " Current status: $STATUS"
    if [ "$STATUS" == "awaiting_manual_score" ]; then break; fi
    sleep 3
    ATTEMPT=$((ATTEMPT+1))
done

if [ "$STATUS" != "awaiting_manual_score" ]; then
    echo "FAIL: Run did not finish in time. Final status: $STATUS"
    exit 1
fi

echo "--- SMOKE TEST PASSED ---"
