#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
if [ ! -f .venv/.lecture-flow-native-ready ] || [ ! -x .venv/bin/gunicorn ]; then ./setup_native.sh; fi
mkdir -p data
echo "Transcribo is available at http://localhost:7860"
echo "Keep this terminal open. Press Ctrl+C to stop."
export TRANSCRIBER_DATA_DIR="$SCRIPT_DIR/data"
export TRANSCRIPTION_ENGINE=mlx
exec .venv/bin/gunicorn --bind 127.0.0.1:7860 --workers 1 --threads 4 --timeout 0 app:app
