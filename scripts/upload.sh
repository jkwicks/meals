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

# 5. Extract structural previews of data schemas (first 35 lines of JSON/CSV files in data/)
{
  echo "# Data Schemas & Reference Structures"
  echo ""
  if [ -d data ]; then
    find data -maxdepth 1 -type f \( -name "*.json" -o -name "*.csv" \) -exec sh -c 'echo "=== Sample Structure: {} ===" && head -n 35 "{}" && echo -e "\n"' \;
  fi
} > data_schemas.md

echo "Bundling complete: python_codebase.md, test_suite.md, project_context.md, data_schemas.md"