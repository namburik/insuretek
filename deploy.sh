#!/bin/bash
# Replaces the EC2 block from run.ksh — pull latest, rebuild, restart containers
set -euo pipefail

APP_DIR=/home/ec2-user/app/paytek
COMPOSE_VERSION="v2.29.7"
COMPOSE_PLUGIN=/usr/local/lib/docker/cli-plugins/docker-compose

# ── Install Docker daemon (AL2023 package) ────────────────────────────────────
if ! command -v docker &>/dev/null; then
  echo "==> Installing Docker..."
  sudo dnf install -y docker
  sudo systemctl enable --now docker
  sudo usermod -aG docker ec2-user
  echo "==> Docker installed. Continuing with new group..."
  exec sg docker "$0 $*"
fi

# ── Install compose plugin binary (always, arch-aware) ───────────────────────
echo "==> Installing docker compose plugin ${COMPOSE_VERSION}..."
ARCH=$(uname -m)   # x86_64 or aarch64
sudo mkdir -p /usr/local/lib/docker/cli-plugins
sudo curl -fsSL \
  "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${ARCH}" \
  -o "$COMPOSE_PLUGIN"
sudo chmod +x "$COMPOSE_PLUGIN"
echo "==> $(docker compose version)"

# ── Deploy ────────────────────────────────────────────────────────────────────
cd "$APP_DIR"

echo "==> Pulling latest code..."
if [ -f docker.env.ec2 ]; then
  TMP_DOCKER_ENV="/tmp/docker.env.ec2.$$(date +%s)"
  cp docker.env.ec2 "$TMP_DOCKER_ENV"
fi

if git ls-files --error-unmatch docker.env.ec2 >/dev/null 2>&1; then
  echo "==> Untracking docker.env.ec2 locally to avoid merge conflicts"
  git rm --cached -f --ignore-unmatch docker.env.ec2 || true
  rm -f docker.env.ec2
fi

git pull origin main

if [ -n "${TMP_DOCKER_ENV:-}" ] && [ -f "$TMP_DOCKER_ENV" ]; then
  mv "$TMP_DOCKER_ENV" docker.env.ec2
fi

echo "==> Ensuring optional EC2 env file exists..."
if [ ! -f docker.env.ec2 ]; then
  touch docker.env.ec2
fi

echo "==> Rebuilding and restarting containers..."
docker compose -f docker-compose.prod.yml up --build -d --remove-orphans

echo "==> Pruning dangling images..."
docker image prune -f

echo "==> Running containers:"
docker compose -f docker-compose.prod.yml ps
