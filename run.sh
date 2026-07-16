#!/usr/bin/env bash
# StoreLens dev launcher
set -e
cd "$(dirname "$0")"
exec uvicorn server.app:app --host 0.0.0.0 --port "${PORT:-8000}"
