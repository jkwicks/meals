#!/usr/bin/env bash
set -e

# 1. Clean up previous bundle files
rm -f python_codebase.md project_context.md data_schemas.md test_suite.md

# 2. Bundle application source code (src/ and scripts/*.py tooling)
{
  echo "# Application Source Code"
  echo ""
  find src scripts -type f -name "*.py" ! -path "*/__pycache__/*" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \;
} > python_codebase.md

# 3. Bundle test suite (tests/)
{
  echo "# Test Suite & Unit Tests"
  echo ""
  find tests -type f -name "*.py" ! -path "*/__pycache__/*" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \;
} > test_suite.md

# 4. Bundle architecture docs, requirements, shell scripts, and Claude workspace rules
{
  echo "# Architecture & System Context"
  echo ""
  [ -f CLAUDE.md ] && echo "=== File: CLAUDE.md ===" && cat CLAUDE.md && echo -e "\n"
  [ -f README.md ] && echo "=== File: README.md ===" && cat README.md && echo -e "\n"
  [ -f requirements.txt ] && echo "=== File: requirements.txt ===" && cat requirements.txt && echo -e "\n"

  # Include operational scripts
  if [ -d scripts ]; then
    find scripts -type f -name "*.sh" ! -name "upload.sh" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \;
  fi

  # Include Claude local rules and skills
  if [ -d .claude ]; then
    find .claude -type f \( -name "*.md" -o -name "*.txt" \) ! -path "*/settings.local.json*" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \;
  fi
} > project_context.md

# 5. Extract config in full, plus structural previews of the generated data
# (first 35 lines each). config/ is hand-edited and small enough to include
# whole; data/ and reference/ are generated or bulk corpora, where a preview
# conveys the shape without the volume.
{
  echo "# Config, Data Schemas & Reference Structures"
  echo ""
  if [ -d config ]; then
    find config -maxdepth 1 -type f -name "*.json" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \;
  fi
  for dir in data reference; do
    [ -d "$dir" ] || continue
    find "$dir" -maxdepth 1 -type f \( -name "*.json" -o -name "*.csv" \) -exec sh -c 'echo "=== Sample Structure: {} ===" && head -n 35 "{}" && echo -e "\n"' \;
  done
} > data_schemas.md

echo "Bundling complete: python_codebase.md, test_suite.md, project_context.md, data_schemas.md"