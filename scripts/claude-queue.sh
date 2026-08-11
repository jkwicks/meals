#!/usr/bin/env bash

# This script lives in scripts/, but .prompts/ is a project-root directory —
# resolve there so the queue works regardless of where it was invoked from.
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Directory containing your ordered markdown prompts
PROMPT_DIR="./.prompts"
RETRY_INTERVAL_SECS=600  # 10 minutes (600 seconds)

# Ensure the prompt directory exists
if [ ! -d "$PROMPT_DIR" ]; then
  echo "Error: Directory '$PROMPT_DIR' does not exist."
  exit 1
fi

# Find all matching files and sort them numerically by prompt number
# Matches: prompt-1.md, prompt-2.md, prompt-03.md, etc.
mapfile -t PROMPT_FILES < <(
  find "$PROMPT_DIR" -type f -name "prompt-*.md" \
  | awk -F'[/.-]' '{print $(NF-1), $0}' \
  | sort -n -k1,1 \
  | cut -d' ' -f2-
)

if [ ${#PROMPT_FILES[@]} -eq 0 ]; then
  echo "No prompt files matching 'prompt-*.md' found in $PROMPT_DIR."
  exit 0
fi

echo "=================================================="
echo "Found ${#PROMPT_FILES[@]} prompt files to process in sequence."
echo "=================================================="

for FILE in "${PROMPT_FILES[@]}"; do
  echo ""
  echo "--------------------------------------------------"
  echo "▶ Processing: $FILE"
  echo "--------------------------------------------------"

  PROMPT_CONTENT=$(cat "$FILE")

  while true; do
    # Temporary log file to capture Claude's output
    TMP_LOG=$(mktemp)

    # Run Claude Code non-interactively with auto-permissions
    claude -p "$PROMPT_CONTENT" --dangerously-skip-permissions > "$TMP_LOG" 2>&1
    EXIT_CODE=$?

    # Print output to terminal in real time
    cat "$TMP_LOG"

    if [ $EXIT_CODE -eq 0 ]; then
      echo ""
      echo "✅ Successfully executed: $FILE"
      rm -f "$TMP_LOG"
      break  # Break retry loop, move to the next file
    else
      # Check if failure was caused by rate limits or usage caps
      if grep -iqE "rate limit|resets at|quota|too many requests" "$TMP_LOG"; then
        echo ""
        echo "⚠️ Rate limit detected while executing $FILE."
        echo "⏳ Pausing for 10 minutes before retrying..."
        rm -f "$TMP_LOG"
        sleep $RETRY_INTERVAL_SECS
      else
        echo ""
        echo "❌ Fatal error occurred in $FILE (Exit Code: $EXIT_CODE)."
        echo "⛔ Halting execution queue. Resolve the issue before re-running."
        rm -f "$TMP_LOG"
        exit $EXIT_CODE  # Stop the entire queue
      fi
    fi
  done
done

echo ""
echo "=================================================="
echo "🎉 All prompts executed successfully!"
echo "=================================================="