#!/bin/bash
# One-time EC2 setup for Amazon Linux 2023. Paste into "User data" when launching
# the instance (or run with sudo once via Session Manager).
#
# Installs Docker + the Compose plugin, adds swap (t3.micro has only 1 GB RAM),
# clones this repo to /opt/watchlist, and runs the first deploy.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/raccha3/watchlist.git}"   # <-- your repo
COMPOSE_VERSION="v2.29.7"

exec > >(tee -a /var/log/watchlist-bootstrap.log) 2>&1
echo "== bootstrap started $(date -Is)"

dnf install -y docker git
systemctl enable --now docker

# Docker Compose v2 plugin (not packaged for AL2023)
arch="$(uname -m)"   # x86_64 or aarch64
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
  "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${arch}"
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
docker compose version

# 1 GB swap so image builds don't get OOM-killed on a 1 GB instance
if ! swapon --show | grep -q /swapfile; then
  dd if=/dev/zero of=/swapfile bs=1M count=1024
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap defaults 0 0' >> /etc/fstab
fi

if [ ! -d /opt/watchlist/.git ]; then
  git clone "$REPO_URL" /opt/watchlist
fi

/opt/watchlist/deploy/deploy.sh
echo "== bootstrap finished $(date -Is)"
