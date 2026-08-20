#!/bin/bash
set -e

echo "--- Starting Docker Smoke Test ---"

# Run id must exist in benchmarks/tetris/runs.yaml (bench_run.py validates it).
RUN_ID="${RUN_ID:-tetris-v1-K-001}"

# 1. Wait for services to be up
echo "Waiting for API to be healthy..."
until [ "$(docker inspect --format='{{.State.Health.Status}}' ai-agentic-harness-lab-api-1 2>/dev/null)" == "healthy" ]; do
    printf "."
    sleep 2
done
echo " API is healthy!"

echo "Waiting for Celery worker to be running..."
until [ "$(docker inspect --format='{{.State.Status}}' ai-agentic-harness-lab-worker-1 2>/dev/null)" == "running" ]; do
    printf "."
    sleep 2
done
echo " Worker is running!"

# 2. Trigger a dry-run via the API (executes bench_run.py --dry-run in a
#    runner container; validates the whole queue + container path cheaply).
echo "Creating dry-run $RUN_ID..."
curl -s -X POST http://localhost:8009/api/runs \
  -H "Content-Type: application/json" \
  -d "{
    \"run_id\": \"$RUN_ID\",
    \"config\": {
      \"case_id\": \"tetris-v1\",
      \"prompt_variant\": \"sdlc\",
      \"executor_model\": \"gateway/lmstudio-qwen3.6-35b-build\",
      \"max_steps\": 1,
      \"score_mode\": \"manual\",
      \"dry_run\": true
    }
  }" | grep -q "\"run_id\":\"$RUN_ID\""

echo "Run $RUN_ID created successfully."

# 3. Wait for the Celery worker + runner container to complete it
echo "Waiting for run to complete (dry-run should reach awaiting_manual_score)..."
MAX_ATTEMPTS=20
ATTEMPT=0
STATE="pending"
until [ "$STATE" == "awaiting_manual_score" ] || [ $ATTEMPT -ge $MAX_ATTEMPTS ]; do
    STATE=$(curl -s "http://localhost:8009/api/runs/$RUN_ID" | grep -o '"state":"[^"]*"' | cut -d'"' -f4)
    echo " Current state: $STATE"
    if [ "$STATE" == "awaiting_manual_score" ]; then break; fi
    sleep 3
    ATTEMPT=$((ATTEMPT+1))
done

if [ "$STATE" != "awaiting_manual_score" ]; then
    echo "FAIL: Run did not finish in time. Final state: $STATE"
    exit 1
fi

echo "--- SMOKE TEST PASSED ---"
