#!/usr/bin/env bash
# Local development server with hot reload (no Docker needed).
# Requires Node 22+. Serves at http://localhost:4321
set -euo pipefail
cd "$(dirname "$0")/../web"
if [ ! -d node_modules ]; then
  echo "Installing dependencies..."
  npm install --no-fund --no-audit
fi
exec npx astro dev --open
