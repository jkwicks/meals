#!/usr/bin/env bash
# Start/stop/status for the meal planner web UI, so you don't have to remember
# venv activation, the right invocation, or how to find/kill it.
#
# NiceGUI (ui_app.py) is the only UI. Streamlit has been removed. The UI can
# generate a week itself now; `python planner.py --help` is the other way in,
# and still the only one that prints shopping lists.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PID_FILE=".nicegui.pid"
LOG_FILE="nicegui.log"
PORT="${MEALS_PORT:-8080}"
DESC="NiceGUI (ui_app.py)"

is_running() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

start() {
    # `return`, not `exit` — `restart` calls stop then start, and an exit here
    # would end the script instead of continuing to the other half.
    if is_running; then
        echo "$DESC already running (PID $(cat "$PID_FILE"), http://localhost:$PORT)."
        return 0
    fi
    if [ ! -d venv ]; then
        echo "No venv/ found — run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
        exit 1
    fi
    source venv/bin/activate

    # ui_app.py reads MEALS_UI_PORT; reload is off in the script it runs, so
    # this stays one process and the PID below is the one to kill.
    MEALS_UI_PORT="$PORT" nohup python ui_app.py > "$LOG_FILE" 2>&1 &

    echo $! > "$PID_FILE"
    disown
    echo "Started $DESC (PID $(cat "$PID_FILE")). UI: http://localhost:$PORT — output in $LOG_FILE"
}

stop() {
    if ! is_running; then
        echo "$DESC not running."
        rm -f "$PID_FILE"
        return 0
    fi
    kill "$(cat "$PID_FILE")"
    rm -f "$PID_FILE"
    echo "Stopped $DESC."
}

status() {
    if is_running; then
        echo "$DESC: running (PID $(cat "$PID_FILE")), http://localhost:$PORT"
    else
        echo "$DESC: not running."
    fi
}

ACTION="${1:-}"

case "$ACTION" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; start ;;
    status)  status ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        echo "  Port override: MEALS_PORT=9000 $0 start"
        exit 1
        ;;
esac
