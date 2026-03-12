#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/trade}"
BRANCH="${BRANCH:-main}"
TARGET_SHA="${TARGET_SHA:-}"
BOT_SERVICE="${BOT_SERVICE:-trade-bot}"
REDIS_SERVICE="${REDIS_SERVICE:-redis}"
DEPLOYMENT_KEY="${DEPLOYMENT_KEY:-tradebot:deployment}"
OPEN_POSITIONS_KEY="${OPEN_POSITIONS_KEY:-tradebot:open_positions}"
DRAIN_POLL_SECONDS="${DRAIN_POLL_SECONDS:-5}"
DRAIN_TIMEOUT_SECONDS="${DRAIN_TIMEOUT_SECONDS:-1800}"
WAIT_TIMEOUT_SECONDS="${WAIT_TIMEOUT_SECONDS:-180}"
ROLLING_SERVICES="${ROLLING_SERVICES:-trade-bot prometheus grafana}"

cd "$REPO_DIR"

compose() {
  docker compose "$@"
}

redis_cli() {
  compose exec -T "$REDIS_SERVICE" redis-cli --raw "$@"
}

set_deployment_state() {
  local status="$1"
  local target_sha="$2"
  redis_cli HSET "$DEPLOYMENT_KEY" \
    status "$status" \
    target_sha "$target_sha" \
    requested_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    requested_by "ops/deploy.sh" >/dev/null
}

clear_deployment_state() {
  set_deployment_state active "$1"
}

drain_requested=0

cleanup() {
  if [ "$drain_requested" -eq 1 ]; then
    clear_deployment_state "$REMOTE_SHA" || true
  fi
}

trap cleanup EXIT

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "working tree is dirty, waiting for clean git state"
  exit 0
fi

git fetch origin "$BRANCH"

LOCAL_SHA="$(git rev-parse HEAD)"

if [ -n "$TARGET_SHA" ]; then
  git rev-parse --verify "${TARGET_SHA}^{commit}" >/dev/null
  git merge-base --is-ancestor "$TARGET_SHA" "origin/$BRANCH"
  REMOTE_SHA="$TARGET_SHA"
else
  REMOTE_SHA="$(git rev-parse "origin/$BRANCH")"
fi

if [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
  echo "no changes to deploy"
  exit 0
fi

CURRENT_BRANCH="$(git branch --show-current)"

if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
  git checkout "$BRANCH"
fi

git merge --ff-only "$REMOTE_SHA"
compose config >/dev/null
compose up -d --wait --wait-timeout "$WAIT_TIMEOUT_SECONDS" postgres redis

bot_container_id="$(compose ps -q "$BOT_SERVICE")"

if [ -n "$bot_container_id" ] && [ "$(docker inspect -f '{{.State.Running}}' "$bot_container_id")" = "true" ]; then
  set_deployment_state draining "$REMOTE_SHA"
  drain_requested=1
  deadline="$(( $(date +%s) + DRAIN_TIMEOUT_SECONDS ))"
  zero_count=0
  while true; do
    open_positions="$(redis_cli HLEN "$OPEN_POSITIONS_KEY" || echo 0)"
    if [ "$open_positions" = "0" ]; then
      zero_count="$((zero_count + 1))"
      if [ "$zero_count" -ge 2 ]; then
        break
      fi
    else
      zero_count=0
    fi
    now="$(date +%s)"
    if [ "$now" -ge "$deadline" ]; then
      echo "drain timeout reached with $open_positions open positions"
      exit 1
    fi
    echo "waiting for open positions to drain: $open_positions"
    sleep "$DRAIN_POLL_SECONDS"
  done
fi

compose up -d --build --wait --wait-timeout "$WAIT_TIMEOUT_SECONDS" $ROLLING_SERVICES
clear_deployment_state "$REMOTE_SHA"
drain_requested=0
compose ps
