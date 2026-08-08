#!/usr/bin/env bash
# Start/stop/status for the Streamlit web UI (app.py), so you don't have to
# remember venv activation, the streamlit invocation, or how to find/kill it.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PID_FILE=".streamlit.pid"
LOG_FILE="streamlit.log"
PORT="${MEALS_PORT:-8501}"

is_running() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start() {
    if is_running; then
        echo "Already running (PID $(cat "$PID_FILE"), http://localhost:$PORT)."
        exit 0
    fi
    if [ ! -d venv ]; then
        echo "No venv/ found — run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
        exit 1
    fi
    source venv/bin/activate
    nohup streamlit run app.py --server.port "$PORT" --server.headless true \
        > "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    disown
    echo "Started (PID $(cat "$PID_FILE")). UI: http://localhost:$PORT — output in $LOG_FILE"
}

stop() {
    if ! is_running; then
        echo "Not running."
        rm -f "$PID_FILE"
        exit 0
    fi
    kill "$(cat "$PID_FILE")"
    rm -f "$PID_FILE"
    echo "Stopped."
}

status() {
    if is_running; then
        echo "Running (PID $(cat "$PID_FILE")), http://localhost:$PORT"
    else
        echo "Not running."
    fi
}

case "${1:-}" in
    start) start ;;
    stop) stop ;;
    restart) stop; start ;;
    status) status ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
