#!/usr/bin/env bash

# Regenerates the AI-assistant bundles in the project root. Run as
# ./scripts/upload.sh. This script lives in scripts/; everything below is
# relative to the project root, so resolve there first.
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# 1. Clean up old bundle files
rm -f python_codebase.md project_context.md data_schemas.md prompts_history.md

# 2. Bundle core Python application source files
# (Includes app logic, repository layers, export logic, and dev tools)
{
  for py_file in src/ui_app.py src/planner.py src/shopping.py src/week.py src/repository.py src/export_menu.py dev/model-list.py; do
    if [ -f "$py_file" ]; then
      echo "=== File: $py_file ==="
      cat "$py_file"
      echo -e "\n"
    fi
  done
} > python_codebase.md

# 3. Bundle architecture documentation, shell scripts, and Claude context configuration
{
  [ -f CLAUDE.md ] && echo "=== File: CLAUDE.md ===" && cat CLAUDE.md && echo -e "\n"
  [ -f README.md ] && echo "=== File: README.md ===" && cat README.md && echo -e "\n"
  [ -f requirements.txt ] && echo "=== File: requirements.txt ===" && cat requirements.txt && echo -e "\n"
  
  # Include active workspace scripts
  for script in scripts/prepare.sh scripts/server.sh scripts/claude-queue.sh scripts/release.sh; do
    if [ -f "$script" ]; then
      echo "=== File: $script ==="
      cat "$script"
      echo -e "\n"
    fi
  done

  # Include Claude local rules and skills
  if [ -d .claude ]; then
    find .claude -type f \( -name "*.md" -o -name "*.txt" \) ! -path "*/settings.local.json*" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \;
  fi
} > project_context.md

# 4. Generate structural schema previews for JSON and CSV configuration/data sources
{
  for data_file in data/config.json data/week_plan.json data/week_plan_next.json data/meal_history.json data/whfoods.json data/recipes_master.json data/models.json data/openrouter_top_50.csv; do
    if [ -f "$data_file" ]; then
      echo "=== Sample Structure: $data_file ==="
      head -n 35 "$data_file"
      echo -e "\n"
    fi
  done
} > data_schemas.md

# 5. Bundle prompt engineering history (active, completed, or failed prompts)
if [ -d .prompts ]; then
  {
    find .prompts -type f -name "*.md" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \;
  } > prompts_history.md
fi
