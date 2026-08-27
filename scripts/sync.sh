#!/usr/bin/env bash
# Run the Garmin/Cronometer biometric sync, and install it as a daily launchd
# job so it happens on days the server is never started.
#
# This is the shape CHANGE-QUEUE.md item 2 chose over the two alternatives:
# nothing here runs inside the UI process, so a Garmin outage or a rate-limited
# Cronometer can't reach a page — the app stays read-only with respect to sync,
# which is the line phase 6e drew on purpose (`ui_settings.py`: "This page
# never syncs"). What the app does do is *report* staleness: Settings ->
# Biometric Sync prints when anything last ran, so a scheduler that stopped is
# visible rather than silent.
#
# Re-running costs nothing for days already covered. `sync_checkpoints` in
# data/biometrics.json records each source's last-checked date and
# `get_sync_date_range` anchors on whichever requested source is furthest
# behind, so a second run the same day resolves to an empty range and issues no
# requests at all.
set -euo pipefail

# scripts/ lives one level under the project root; every path below is relative
# to the root, not to this script. Same reasoning as server.sh.
cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$(pwd -P)"

mkdir -p logs
LOG_FILE="logs/sync.log"
LABEL="${MEALS_SYNC_LABEL:-com.meals.sync}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
# Daily, at this local time. Overridable the same way server.sh takes
# MEALS_PORT — read at install time and baked into the plist, so changing it
# means re-running `install`.
HOUR="${MEALS_SYNC_HOUR:-7}"
MINUTE="${MEALS_SYNC_MINUTE:-30}"

run() {
    if [ ! -d venv ]; then
        echo "No venv/ found — run: python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt" >&2
        exit 1
    fi
    # A banner per run, because this file is what a "did the scheduler stop?"
    # question is answered against. launchd owns the redirect (see the plist
    # below), so a hand-run prints to the terminal and a scheduled one lands
    # in $LOG_FILE — one code path either way.
    echo "=== meals sync $(date '+%Y-%m-%d %H:%M:%S') ==="
    # Both sources in one invocation, with no --date and no --catchup: that is
    # the bare, scheduled shape the CLI is written for, where catchup stays on
    # so a missed day is backfilled rather than lost forever. Each source is
    # reported independently, so one failing doesn't cost the other.
    ./venv/bin/python src/integrations/sync_service.py --sync-garmin --sync-cronometer
}

install_job() {
    mkdir -p "$HOME/Library/LaunchAgents"
    # Written here rather than shipped as a template with a placeholder path:
    # the plist needs this checkout's absolute path, and a template is a thing
    # to hand-edit correctly every time it moves.
    cat > "$PLIST" <<PLIST_BODY
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$ROOT/scripts/sync.sh</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$ROOT</string>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>$HOUR</integer>
        <key>Minute</key>
        <integer>$MINUTE</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>$ROOT/$LOG_FILE</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/$LOG_FILE</string>
</dict>
</plist>
PLIST_BODY

    # bootout first so `install` is repeatable — bootstrap fails outright if
    # the label is already loaded, which it will be on every re-install.
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$PLIST"
    printf 'Installed %s — daily at %02d:%02d, logging to %s\n' "$LABEL" "$HOUR" "$MINUTE" "$LOG_FILE"
    echo "A run missed while the machine was asleep fires once on wake."
}

uninstall_job() {
    launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed $LABEL."
}

status() {
    if [ ! -f "$PLIST" ]; then
        echo "$LABEL: not installed. Run: ./scripts/sync.sh install"
        return 0
    fi
    if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
        echo "$LABEL: installed and loaded ($PLIST)"
    else
        echo "$LABEL: plist present but not loaded — run: ./scripts/sync.sh install"
    fi
    # The dates the app itself reads. Settings -> Biometric Sync draws the same
    # field; this is the terminal answer to the same question.
    ./venv/bin/python - <<'PY'
import json, pathlib
path = pathlib.Path("data/biometrics.json")
if not path.exists():
    print("  data/biometrics.json: absent — nothing has ever synced.")
else:
    checkpoints = (json.loads(path.read_text()).get("sync_checkpoints") or {})
    if not checkpoints:
        print("  no sync checkpoints — nothing has ever synced.")
    for source, stamp in sorted(checkpoints.items()):
        print(f"  {source}: last checked {stamp}")
PY
}

case "${1:-run}" in
    run)       run ;;
    install)   install_job ;;
    uninstall) uninstall_job ;;
    status)    status ;;
    *)
        echo "Usage: $0 {run|install|uninstall|status}"
        echo "  Schedule override (at install time): MEALS_SYNC_HOUR=6 MEALS_SYNC_MINUTE=0 $0 install"
        exit 1
        ;;
esac
