#!/usr/bin/env bash
# Start/stop/status for the meal planner web UI, so you don't have to remember
# venv activation, the right invocation, or how to find/kill it.
#
# Two UIs coexist during the NiceGUI migration, and each gets its own port, PID
# file and log so both can run at once. That matters: ui_app.py is read-only
# and still can't generate a week, so the Streamlit app has to stay reachable
# to produce the week_plan.json the NiceGUI one renders.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

DEFAULT_UI="nicegui"

select_ui() {
    case "$1" in
        nicegui)
            PID_FILE=".nicegui.pid"
            LOG_FILE="nicegui.log"
            PORT="${MEALS_PORT:-8080}"
            DESC="NiceGUI (ui_app.py)"
            ;;
        streamlit)
            PID_FILE=".streamlit.pid"
            LOG_FILE="streamlit.log"
            PORT="${MEALS_PORT:-8501}"
            DESC="Streamlit (app.py)"
            ;;
        *)
            echo "Unknown UI '$1' — expected 'nicegui' or 'streamlit'." >&2
            exit 1
            ;;
    esac
}

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

    if [ "$UI" = "nicegui" ]; then
        # ui_app.py reads MEALS_UI_PORT; reload is off in the script it runs,
        # so this stays one process and the PID below is the one to kill.
        MEALS_UI_PORT="$PORT" nohup python ui_app.py > "$LOG_FILE" 2>&1 &
    else
        nohup streamlit run app.py --server.port "$PORT" --server.headless true \
            > "$LOG_FILE" 2>&1 &
    fi

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

# Status reports on both UIs regardless of which one was named — during the
# migration "is it up?" almost always means "which of them is up?".
status_all() {
    for ui in nicegui streamlit; do
        select_ui "$ui"
        status
    done
}

ACTION="${1:-}"
UI="${2:-$DEFAULT_UI}"

case "$ACTION" in
    start)   select_ui "$UI"; start ;;
    stop)    select_ui "$UI"; stop ;;
    restart) select_ui "$UI"; stop; start ;;
    status)  status_all ;;
    *)
        echo "Usage: $0 {start|stop|restart|status} [nicegui|streamlit]"
        echo "  UI defaults to $DEFAULT_UI. Port override: MEALS_PORT=9000 $0 start"
        exit 1
        ;;
esac
