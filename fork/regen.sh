#!/usr/bin/env bash
#
# Regenerate fork/fork.patch from the current working tree.
#
#     fork/regen.sh <upstream-tag-or-ref>
#
# fork/fork.patch is the source of truth for the fork's source changes — the
# release branch is force-pushed away on every release, so commits on it are
# not. To change what the fork does:
#
#   1. Check out the upstream tag the patch is currently based on.
#   2. Run fork/apply.sh, then edit the code normally and test it.
#   3. Run fork/regen.sh <that-tag> to write your edits back into the patch.
#
# Also use this when a release fails because a hunk rejected: apply the patch to
# the new upstream tag, fix up the rejected hunk by hand, and regenerate against
# that tag. That re-bases the patch, so the stale context is gone for good.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ $# -ne 1 ]; then
  echo "usage: fork/regen.sh <upstream-tag-or-ref>" >&2
  exit 1
fi
BASE="$1"

git rev-parse -q --verify "$BASE" >/dev/null \
  || { echo "regen.sh: '$BASE' is not a valid ref" >&2; exit 1; }

# Files the patch owns. Everything else the fork changes is handled by
# fork/apply.sh as a whole-file copy or a single-line rewrite, and must stay out
# of the patch so it cannot carry stale context.
PATHS=(
  crates/ruff/src/diagnostics.rs
  crates/ruff_markdown/src/lib.rs
)

git diff "$BASE" -- "${PATHS[@]}" > fork/fork.patch

if [ ! -s fork/fork.patch ]; then
  echo "regen.sh: refusing to write an empty patch — is the tree unmodified?" >&2
  git checkout -- fork/fork.patch 2>/dev/null || true
  exit 1
fi

echo "Wrote fork/fork.patch against $BASE:"
grep -c '^@@' fork/fork.patch | xargs printf '    %s hunk(s) across\n'
grep '^+++ b/' fork/fork.patch | sed 's|^+++ b/|        |'
