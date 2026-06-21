#!/data/data/com.termux/files/usr/bin/bash
# ================================================================
# NERMANA Simple Update Script
# Detects updates and applies them while preserving user files
# ================================================================

set -Eeuo pipefail
trap 'echo -e "\n \e[31m✖\e[0m Update interrupted"; exit 1' INT TERM

# ── Terminal formatting ──────────────────────────
BOLD="\e[1m"; DIM="\e[2m"; RESET="\e[0m"
GREEN="\e[32m"; RED="\e[31m"; CYAN="\e[36m"; YELLOW="\e[33m"

ok()      { echo -e " ${GREEN}✔${RESET} $1"; }
warn()    { echo -e " ${YELLOW}⚠${RESET} $1"; }
fail()    { echo -e " ${RED}✖${RESET} $1"; exit 1; }
info()    { echo -e " ${CYAN}ℹ${RESET} $1"; }

# ── Paths ───────────────────────────────────────
NERMANA_DIR="$HOME/nermana"
cd "$NERMANA_DIR" || fail "Cannot change to $NERMANA_DIR"

# Check if we're in a git repo
if [ ! -d ".git" ]; then
    fail "Not a git repository. Cannot perform update."
fi

# Fetch latest from origin
info "Fetching latest from origin..."
if ! git fetch origin --tags 2>&1; then
    fail "Failed to fetch from origin. Check your connection."
fi

# Check if we're behind
BEHIND_COUNT=$(git rev-list --count HEAD..origin/master 2>/dev/null || \
               git rev-list --count HEAD..origin/main 2>/dev/null || echo "0")

if [ "$BEHIND_COUNT" -eq 0 ]; then
    ok "Already up to date"
    exit 0
fi

info "Found $BEHIND_COUNT update(s) available"

# Attempt fast-forward pull (preserves local changes to tracked files only if no conflict)
info "Attempting to update..."
if git merge --ff-only origin/master 2>/dev/null || git merge --ff-only origin/main 2>/dev/null; then
    ok "Update applied successfully"

    # Show what changed (optional)
    # git diff --stat HEAD@{1} HEAD

    info "Run 'nermana restart' to restart services with the new version"
    exit 0
else
    warn "Fast-forward failed. This usually means you have local changes to tracked files."
    echo ""
    echo "Options:"
    echo "  1. Stash your changes, update, then restore: "
    echo "        git stash"
    echo "        ./update.sh"
    echo "        git stash pop"
    echo ""
    echo "  2. If you don't care about local changes to tracked files, reset: "
    echo "        git reset --hard origin/master"
    echo ""
    echo "  3. Commit your changes and then update: "
    echo "        git add <files>"
    echo "        git commit -m \"Your changes\""
    echo "        ./update.sh"
    echo ""
    fail "Update aborted to preserve your files"
fi