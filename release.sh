#!/usr/bin/env bash
set -euo pipefail

# Usage: ./release.sh <version_type: patch|minor|major> "Technical release notes" "Plain english release notes"

VERSION_TYPE="${1:-patch}"
TECH_NOTES="${2:-No technical notes provided.}"
PLAIN_NOTES="${3:-Routine maintenance and bug fixes.}"

TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
BRANCH_NAME="release/${VERSION_TYPE}-${TIMESTAMP}"

echo "=================================================="
echo "Creating release branch: ${BRANCH_NAME}"
echo "=================================================="

# Check for uncommitted changes
if [ -z "$(git status --porcelain)" ]; then
  echo "No changes detected in repository. Exiting."
  exit 0
fi

# Create and checkout new branch
git checkout -b "${BRANCH_NAME}"

# Stage all tracked and untracked changes (excluding .gitignore entries)
git add .

# Construct structured commit message with release notes
COMMIT_MSG=$(cat <<EOF
release(${VERSION_TYPE}): automated deployment ${TIMESTAMP}

[Plain English Summary]
${PLAIN_NOTES}

[Technical Summary]
${TECH_NOTES}

Automated via release.sh on $(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF
)

# Commit changes
git commit -m "${COMMIT_MSG}"

# Push branch to remote origin
echo "Pushing branch ${BRANCH_NAME} to remote origin..."
git push -u origin "${BRANCH_NAME}"

echo ""
echo "=================================================="
echo "🎉 Release ${BRANCH_NAME} pushed successfully!"
echo "=================================================="