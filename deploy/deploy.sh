#!/bin/bash
# Deploy the latest commit on main. Runs on the EC2 instance as root, either by hand
# (Session Manager: `sudo /opt/watchlist/deploy/deploy.sh`) or from GitHub Actions via SSM.
#
# 1. Pull the latest code
# 2. Read DB settings + password from SSM Parameter Store (using the instance's IAM role,
#    so there are no AWS keys on the box) and write them to a root-only .env file
# 3. Build the image and (re)start the containers
# 4. Wait until the app's Docker health check (which calls /health) reports healthy
set -euo pipefail

APP_DIR=/opt/watchlist
PARAM_PREFIX="${PARAM_PREFIX:-/watchlist}"
cd "$APP_DIR"

echo "== pulling latest code"
git fetch --quiet origin main
git reset --hard --quiet origin/main
VERSION="$(git rev-parse --short HEAD)"
echo "   version $VERSION"

echo "== reading config from SSM Parameter Store ($PARAM_PREFIX/*)"
TOKEN="$(curl -sf -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')"
REGION="$(curl -sf -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)"

param() {  # param <name> [default]
  aws ssm get-parameter --region "$REGION" --with-decryption \
    --name "$PARAM_PREFIX/$1" --query Parameter.Value --output text 2>/dev/null || echo "${2-}"
}

DB_HOST="$(param db_host)"
DB_PASSWORD="$(param db_password)"
if [ -z "$DB_HOST" ] || [ -z "$DB_PASSWORD" ]; then
  echo "ERROR: $PARAM_PREFIX/db_host or $PARAM_PREFIX/db_password missing (or IAM role can't read them)" >&2
  exit 1
fi

umask 077   # .env readable by root only
cat > "$APP_DIR/.env" <<EOF
DB_HOST=$DB_HOST
DB_PORT=$(param db_port 5432)
DB_NAME=$(param db_name watchlist)
DB_USER=$(param db_user watchlist)
DB_PASSWORD=$DB_PASSWORD
DB_SSLMODE=require
APP_VERSION=$VERSION
EOF
export SITE_ADDRESS="$(param site_address '')"

echo "== building image watchlist:$VERSION"
docker build --quiet --build-arg APP_VERSION="$VERSION" -t "watchlist:$VERSION" -t watchlist:latest .

echo "== starting containers"
docker compose -p watchlist -f deploy/docker-compose.prod.yml up -d --remove-orphans

echo "== waiting for health check (through Caddy, the same path users take)"
# Checking only the app container isn't enough: if Caddy fails to start, the app is
# "healthy" but nothing answers on port 80. So check end to end via Caddy.
# -k / --resolve: works whether Caddy serves plain HTTP on :80 or HTTPS for a domain.
check_url() {
  if [ -n "${SITE_ADDRESS:-}" ]; then
    curl -fsS -m 5 -k --resolve "$SITE_ADDRESS:443:127.0.0.1" "https://$SITE_ADDRESS/health"
  else
    curl -fsS -m 5 http://127.0.0.1/health
  fi
}
for i in $(seq 1 30); do
  if body="$(check_url 2>/dev/null)"; then
    echo "   healthy after ~$((i * 5))s -- deployed $VERSION"
    echo "   $body"
    docker image prune -f --filter "until=168h" >/dev/null
    exit 0
  fi
  sleep 5
done

echo "ERROR: /health not reachable through Caddy after 150s. Container status and logs:" >&2
docker compose -p watchlist -f deploy/docker-compose.prod.yml ps -a >&2
docker compose -p watchlist -f deploy/docker-compose.prod.yml logs --tail 30 >&2
exit 1
