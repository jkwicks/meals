#!/usr/bin/env bash
set -e

# 1. Clean up previous bundle files
rm -f python_codebase.md project_context.md data_schemas.md test_suite.md

# 2. Bundle application source code (src/ and scripts/)
{
  echo "# Application Source Code & Core Modules"
  echo ""
  find src scripts -type f -name "*.py" ! -path "*/__pycache__/*" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \;
} > python_codebase.md

# 3. Bundle test suite & test fixtures (tests/)
{
  echo "# Test Suite, Integration Tests & Fixtures"
  echo ""
  find tests -type f \( -name "*.py" -o -name "*.json" \) ! -path "*/__pycache__/*" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \;
} > test_suite.md

# 4. Bundle architecture docs, future ideas, requirements, operational scripts, and Claude workspace rules
{
  echo "# Architecture, Workspace Rules & System Context"
  echo ""
  [ -f CLAUDE.md ] && echo "=== File: CLAUDE.md ===" && cat CLAUDE.md && echo -e "\n"
  [ -f README.md ] && echo "=== File: README.md ===" && cat README.md && echo -e "\n"
  [ -f future-ideas.md ] && echo "=== File: future-ideas.md ===" && cat future-ideas.md && echo -e "\n"
  [ -f requirements.txt ] && echo "=== File: requirements.txt ===" && cat requirements.txt && echo -e "\n"

  # Include operational shell scripts
  if [ -d scripts ]; then
    find scripts -type f -name "*.sh" ! -name "upload.sh" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \;
  fi

  # Include Claude local rules and skills
  if [ -d .claude ]; then
    find .claude -type f \( -name "*.md" -o -name "*.txt" \) ! -path "*/settings.local.json*" -exec sh -c 'echo "=== File: {} ===" && cat "{}" && echo -e "\n"' \;
  fi
} > project_context.md

# 5. Extract structural previews of configuration, reference files, and data state
{
  echo "# Configuration & Data Schema Previews"
  echo ""
  
  # Sample all modular config files
  if [ -d config ]; then
    echo "## Config Schemas (config/)"
    find config -maxdepth 1 -type f -name "*.json" -exec sh -c 'echo "=== Sample Structure: {} ===" && head -n 35 "{}" && echo -e "\n"' \;
  fi

  # Sample reference datasets
  if [ -d reference ]; then
    echo "## Reference Data (reference/)"
    find reference -maxdepth 1 -type f -name "*.json" -exec sh -c 'echo "=== Sample Structure: {} ===" && head -n 35 "{}" && echo -e "\n"' \;
  fi

  # Sample active data stores
  if [ -d data ]; then
    echo "## State Data (data/)"
    find data -maxdepth 1 -type f \( -name "*.json" -o -name "*.csv" \) -exec sh -c 'echo "=== Sample Structure: {} ===" && head -n 35 "{}" && echo -e "\n"' \;
  fi
} > data_schemas.md

echo "Bundling complete: python_codebase.md, test_suite.md, project_context.md, data_schemas.md"