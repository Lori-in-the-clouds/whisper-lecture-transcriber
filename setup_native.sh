#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
.venv/bin/pip install -r requirements-native.txt
touch .venv/.lecture-flow-native-ready
echo "Servizio MLX configurato. Ora esegui: ./start_app.sh"
