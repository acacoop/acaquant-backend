#!/bin/bash
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export HOME="/root"

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

LOG="$DIR/start_all.log"
exec >> "$LOG" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') Iniciando ==="

"$DIR/venv/bin/python" "$DIR/main_valores.py" &
"$DIR/venv/bin/streamlit" run "$DIR/streamlit_app.py" --server.port 8501 --server.headless true &

echo "PIDs: $(jobs -p)"
wait
