#!/usr/bin/env bash
# Production-style run: builds both images (web build also runs smoke tests)
# and starts the stack exactly as it would run deployed.
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up --build -d
echo "Site is live at http://localhost:8090"
echo "Console (auth-gated): http://localhost:8090/app"
echo "API health: http://localhost:8090/api/healthz"
docker compose ps
