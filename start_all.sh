#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

"$DIR/venv/bin/python" "$DIR/main_valores.py" &

"$DIR/venv/bin/streamlit" run "$DIR/streamlit_app.py" --server.port 8501 --server.headless true &

wait
