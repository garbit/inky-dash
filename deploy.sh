#!/usr/bin/env bash
# Deploy the current working tree to the Pi and restart the service.
#
#   ./deploy.sh              build frontend, run backend tests, sync, restart
#   ./deploy.sh --skip-build backend-only change: skip the frontend build
#   ./deploy.sh --skip-tests skip the backend test run
#
# The Pi never builds anything (512MB RAM): frontend/dist is built here and
# shipped as-is. Runtime state on the Pi (config.json, data/, output/) is
# never touched.
set -euo pipefail
cd "$(dirname "$0")"

# .deploy.env is written by setup.sh; an explicit PI_HOST env var wins.
if [ -z "${PI_HOST:-}" ] && [ -f .deploy.env ]; then
  # shellcheck disable=SC1091
  . ./.deploy.env
fi
[ -n "${PI_HOST:-}" ] || { echo "PI_HOST not set — run ./setup.sh first, or: PI_HOST=user@host ./deploy.sh" >&2; exit 2; }
PI_PATH="${PI_PATH:-~/inky-dash}"

skip_build=false
skip_tests=false
for arg in "$@"; do
  case "$arg" in
    --skip-build) skip_build=true ;;
    --skip-tests) skip_tests=true ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if ! $skip_tests; then
  echo "==> backend tests"
  (cd backend && uv run pytest -q)
fi

if ! $skip_build; then
  echo "==> frontend build"
  (cd frontend && npm run build)
elif [ ! -d frontend/dist ]; then
  echo "frontend/dist missing — run without --skip-build first" >&2
  exit 1
fi

echo "==> rsync to $PI_HOST:$PI_PATH"
rsync -az --delete \
  --exclude '.git' --exclude '.venv' --exclude 'node_modules' \
  --exclude '__pycache__' --exclude '.pytest_cache' --exclude '.ruff_cache' \
  --exclude 'output' --exclude 'config.json' --exclude 'data' \
  --exclude 'frontend/src' --exclude 'frontend/public' \
  ./ "$PI_HOST:$PI_PATH/"

echo "==> uv sync + restart"
ssh "$PI_HOST" "cd $PI_PATH/backend && PATH=\$HOME/.local/bin:\$PATH uv sync --extra pi --quiet \
  && if systemctl list-unit-files inky-dash.service --no-legend 2>/dev/null | grep -q inky-dash; then \
       sudo systemctl restart inky-dash; \
     else \
       echo 'inky-dash service not installed yet — run ./setup.sh to finish first-time setup'; \
     fi"

# Provisioning (the unit file, firewall rules) belongs to setup.sh — but an
# install predating the move to port 80 would silently keep serving on 8000.
if ssh "$PI_HOST" "grep -q -- '--port 80\b' /etc/systemd/system/inky-dash.service" 2>/dev/null; then
  echo "==> done — http://${PI_HOST#*@}"
else
  echo "==> done — http://${PI_HOST#*@}:8000"
  echo "    note: this Pi still serves on port 8000 — run ./setup.sh to move it to port 80"
fi
