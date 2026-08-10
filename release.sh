#!/usr/bin/env bash
set -euo pipefail

# Usage: ./release.sh <patch|minor|major> "Technical release notes" "Plain english release notes"

BUMP_TYPE="${1:-patch}"
TECH_NOTES="${2:-Routine technical updates.}"
PLAIN_NOTES="${3:-Routine maintenance and bug fixes.}"

MAIN_BRANCH="main"

# Ensure gh CLI is installed
if ! command -v gh &> /dev/null; then
  echo "❌ GitHub CLI ('gh') is required. Install via 'brew install gh' and run 'gh auth login'."
  exit 1
fi

# Fetch latest tags and main branch
git fetch origin --tags
git checkout "${MAIN_BRANCH}"
git pull origin "${MAIN_BRANCH}"

# Get latest SemVer tag or default to v0.0.0
LATEST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.0.0")
VERSION_NUM="${LATEST_TAG#v}"

IFS='.' read -r MAJOR MINOR PATCH <<< "${VERSION_NUM}"

case "${BUMP_TYPE}" in
  major)
    MAJOR=$((MAJOR + 1))
    MINOR=0
    PATCH=0
    ;;
  minor)
    MINOR=$((MINOR + 1))
    PATCH=0
    ;;
  patch)
    PATCH=$((PATCH + 1))
    ;;
  *)
    echo "❌ Unknown bump type '${BUMP_TYPE}'. Use major, minor, or patch."
    exit 1
    ;;
esac

NEW_TAG="v${MAJOR}.${MINOR}.${PATCH}"
BRANCH_NAME="release/${NEW_TAG}"

echo "=================================================="
echo "Preparing Release ${NEW_TAG} on branch ${BRANCH_NAME}"
echo "=================================================="

# Create release branch
git checkout -b "${BRANCH_NAME}"
git add .

if git diff-index --quiet HEAD --; then
  echo "No changes to commit, proceeding with existing branch commits."
else
  COMMIT_MSG=$(cat <<EOF
release(${NEW_TAG}): ${BUMP_TYPE} update

[Plain English Summary]
${PLAIN_NOTES}

[Technical Summary]
${TECH_NOTES}
EOF
  )
  git commit -m "${COMMIT_MSG}"
fi

# Push branch to remote
git push -u origin "${BRANCH_NAME}"

# Release Body Formatting
RELEASE_BODY=$(cat <<EOF
## [Plain English Summary]
${PLAIN_NOTES}

## [Technical Summary]
${TECH_NOTES}
EOF
)

echo "Creating GitHub Pull Request..."
PR_URL=$(gh pr create \
  --title "release(${NEW_TAG}): Automated Release" \
  --body "${RELEASE_BODY}" \
  --base "${MAIN_BRANCH}" \
  --head "${BRANCH_NAME}")

echo "Pull Request created: ${PR_URL}"

echo "Merging Pull Request into ${MAIN_BRANCH}..."
gh pr merge "${PR_URL}" --merge --delete-branch

echo "Switching to ${MAIN_BRANCH} and pulling merged changes..."
git checkout "${MAIN_BRANCH}"
git pull origin "${MAIN_BRANCH}"

echo "Creating and pushing SemVer GitHub Release ${NEW_TAG}..."
gh release create "${NEW_TAG}" \
  --title "${NEW_TAG}" \
  --notes "${RELEASE_BODY}"

echo ""
echo "=================================================="
echo "🎉 Release ${NEW_TAG} created, merged, and published!"
echo "=================================================="