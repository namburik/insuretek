#!/bin/bash
# One-time Let's Encrypt certificate bootstrap for EC2.
# Run this ONCE after first deploy. After certs exist, the normal
# docker-compose.prod.yml (with nginx.conf) handles everything including renewals.
#
# Usage: ./scripts/init_ssl.sh <domain> <email>
#   e.g. ./scripts/init_ssl.sh app.example.com admin@example.com
set -euo pipefail

DOMAIN="${1:?Usage: $0 <domain> <email>}"
EMAIL="${2:?Usage: $0 <domain> <email>}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# ── Step 1: start nginx with HTTP-only bootstrap config ──────────────────────
echo "==> Starting nginx (HTTP-only) for ACME challenge..."
docker run -d --name nginx-init \
  -p 80:80 \
  -v "$APP_DIR/nginx/nginx.init.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v "$APP_DIR/certbot/www:/var/www/certbot" \
  nginx:alpine

# ── Step 2: obtain certificate via webroot challenge ─────────────────────────
echo "==> Requesting Let's Encrypt certificate for $DOMAIN..."
docker run --rm \
  -v "$APP_DIR/certbot/conf:/etc/letsencrypt" \
  -v "$APP_DIR/certbot/www:/var/www/certbot" \
  certbot/certbot certonly \
    --webroot --webroot-path=/var/www/certbot \
    --email "$EMAIL" --agree-tos --no-eff-email \
    -d "$DOMAIN"

# ── Step 3: stop bootstrap nginx ─────────────────────────────────────────────
echo "==> Stopping bootstrap nginx..."
docker stop nginx-init && docker rm nginx-init

# ── Step 4: replace YOUR_DOMAIN placeholder in nginx.conf ───────────────────
echo "==> Patching nginx/nginx.conf with domain $DOMAIN..."
sed -i "s/YOUR_DOMAIN/$DOMAIN/g" "$APP_DIR/nginx/nginx.conf"

echo "==> Done. Now bring up the full stack:"
echo "    docker compose -f docker-compose.prod.yml up --build -d"
