#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/opt/trade}"
BRANCH="${BRANCH:-main}"
TARGET_SHA="${TARGET_SHA:-}"

cd "$REPO_DIR"

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
docker compose config >/dev/null
docker compose up -d --build --remove-orphans
docker compose ps
