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
# MLX/Metal relies on macOS XPC services that are not reliable after Gunicorn's
# worker fork. Run the local app in one native Python process instead.
exec .venv/bin/python app.py
