#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Rebuilding and restarting containers..."
docker compose -f docker-compose.yml up --build -d --remove-orphans

echo "==> Pruning dangling images..."
docker image prune -f

echo "==> Running containers:"
docker compose -f docker-compose.yml ps
