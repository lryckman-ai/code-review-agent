#!/usr/bin/env bash
# One-shot convenience: start A2A servers in background, run the review, then clean up.
# Usage: ./start.sh <file_to_review>

set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/venv/bin/python"

if [[ -z "$1" ]]; then
    echo "Usage: ./start.sh <file_to_review>"
    exit 1
fi

echo "Starting A2A servers..."
"$PY" "$DIR/run_servers.py" &
SERVERS_PID=$!
trap "kill $SERVERS_PID 2>/dev/null; exit" INT TERM EXIT

"$PY" "$DIR/main.py" "$1"
