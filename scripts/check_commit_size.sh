#!/usr/bin/env bash
# Soft files-per-commit cap: warn (never fail) when a commit stages many files.
# A commit spanning many files/areas is the big-bang anti-pattern the operating
# model eliminates — one task, one commit. This only prints a warning; it always
# exits 0 so it can never block a legitimately large commit.
set -euo pipefail

CAP="${AGENTSHIP_COMMIT_FILE_CAP:-8}"
staged_count="$(git diff --cached --name-only | grep -c . || true)"

if [ "${staged_count}" -gt "${CAP}" ]; then
  echo "warning: this commit stages ${staged_count} files (soft cap ${CAP})." >&2
  echo "         one task = one commit — consider splitting if this spans areas." >&2
fi

exit 0
