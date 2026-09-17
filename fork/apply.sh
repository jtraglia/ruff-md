#!/usr/bin/env bash
#
# Turn a clean checkout of an upstream ruff release into ruff-md.
#
# Run from the repo root with the working tree checked out at an upstream
# release tag (detached HEAD is fine):
#
#     fork/apply.sh
#
# There is no merging anywhere in here. Every change is one of four dumb
# operations, ordered by how much each can go wrong:
#
#   1. Copy in a file that exists only in this fork. Cannot fail — there is no
#      upstream version to diverge from.
#   2. Rewrite a single known-constant line. Cannot fail unless upstream renames
#      the thing, which is verified immediately afterwards. Deliberately not a
#      patch hunk: a hunk would carry neighbouring lines as context, and for
#      pyproject.toml that means the `version` line, which changes every release
#      and is exactly what used to break this pipeline.
#   3. Rewrite a file structurally — find a syntactic landmark and edit relative
#      to it, without reading the values being replaced, so upstream editing
#      them cannot matter. Used where a patch hunk anchors to something upstream
#      keeps extending.
#   4. Apply fork/fork.patch with `patch --fuzz`, which searches for each hunk's
#      surrounding context rather than trusting line numbers.
#
# Steps 1-3 either succeed or abort with a specific message. Only step 4 can
# reject anything, and a rejected hunk does NOT stop this script: a hunk also
# rejects when upstream has implemented that change itself, in which case the
# right move is to carry on. Rejections are reported and listed in
# fork-rejects.txt. Whether the result is actually good is decided afterwards by
# building it and checking that markdown linting still works — a rejected hunk
# can still compile while silently dropping the feature.
set -euo pipefail

cd "$(dirname "$0")/.."

fail() {
  echo "fork/apply.sh: $*" >&2
  exit 1
}

[ -f fork/fork.patch ] || fail "fork/fork.patch is missing"

# ---------------------------------------------------------------------------
# 1. Workflow files that exist only in this fork.
#
# Copied whole because upstream has no version of them to diverge from. Files
# upstream *does* own are never copied over — see steps 2-4.
#
# They live in fork/workflows/ rather than a mirrored .github/workflows/ path so
# that linters keyed to that path (zizmor, actionlint) check the real files
# once, not these copies as well.
# ---------------------------------------------------------------------------
echo "==> Copying fork-only workflows"
for src in fork/workflows/*.yml; do
  cp "$src" ".github/workflows/$(basename "$src")"
  echo "    .github/workflows/$(basename "$src")"
done

# ---------------------------------------------------------------------------
# 2. Single-line rewrites of things upstream will not rename.
#
# Done unconditionally instead of as patch hunks. A hunk here would carry
# neighbouring lines as context — for pyproject.toml that means the `version`
# line, which changes every single release and is exactly what used to break
# this pipeline. A blind rewrite has no context to get stale.
# ---------------------------------------------------------------------------
echo "==> Rewriting fork constants"

# The PyPI distribution name. `module-name` under [tool.maturin] and the `ruff`
# binary name are deliberately left alone.
sed -i.bak 's/^name = "ruff"$/name = "ruff-md"/' pyproject.toml && rm -f pyproject.toml.bak
grep -q '^name = "ruff-md"$' pyproject.toml \
  || fail 'pyproject.toml has no `name = "ruff-md"` after the rewrite'
echo '    pyproject.toml: name = "ruff-md"'

# Wheel filenames come out as ruff_md-*.whl (PEP 491 normalises the hyphen), so
# the globs in build-binaries.yml have to match.
sed -i.bak 's/^  PACKAGE_NAME: ruff$/  PACKAGE_NAME: ruff_md/' \
  .github/workflows/build-binaries.yml && rm -f .github/workflows/build-binaries.yml.bak
grep -q '^  PACKAGE_NAME: ruff_md$' .github/workflows/build-binaries.yml \
  || fail "build-binaries.yml has no PACKAGE_NAME: ruff_md after the rewrite"
echo "    build-binaries.yml: PACKAGE_NAME: ruff_md"

# ---------------------------------------------------------------------------
# 3. Upstream's release.yml, rewritten in place.
#
# Not a frozen copy: release.yml is upstream's, it changes, and it calls other
# upstream workflows whose inputs change with it. The fork's edits to it are
# re-derived from whatever upstream ships. Exits non-zero if the release
# pipeline no longer has the jobs the fork depends on.
# ---------------------------------------------------------------------------
echo "==> Rewriting release.yml for this fork"
python3 fork/release_workflow.py .github/workflows/release.yml \
  || fail "could not rewrite release.yml (see above)"

# ---------------------------------------------------------------------------
# 3b. Let `ruff check` accept markdown files.
#
# Not a patch hunk: it anchors to the list of lintable source types, which
# upstream extends whenever ruff learns a new file type. That was the only hunk
# in fork.patch that ever broke — see fork/accept_markdown.py.
# ---------------------------------------------------------------------------
echo "==> Enabling markdown in the source-type filter"
python3 fork/accept_markdown.py crates/ruff/src/commands/check.rs \
  || fail "could not enable markdown in check.rs (see above)"

# ---------------------------------------------------------------------------
# 4. The fork's remaining source changes.
# ---------------------------------------------------------------------------
echo "==> Applying fork/fork.patch"
rm -f fork-rejects.txt

# --forward skips hunks that are already applied instead of prompting, which
# would hang in CI. --fuzz=3 lets patch ignore context lines that no longer
# match, so upstream inserting code next to the fork's additions is fine.
patch -p1 --forward --fuzz=3 --no-backup-if-mismatch -i fork/fork.patch || true

find . -name '*.rej' -not -path './target/*' | sed 's|^\./||' | sort > fork-rejects.txt
if [ -s fork-rejects.txt ]; then
  echo
  echo "==> WARNING: $(grep -c '' fork-rejects.txt) file(s) had rejected hunks:"
  sed 's|^|        |' fork-rejects.txt
  echo "    Either upstream refactored the code the fork hooks into, or it"
  echo "    implemented that change itself. Verification decides which."
else
  rm -f fork-rejects.txt
  echo "    applied cleanly"
fi

echo
echo "==> Done. Verify before releasing:"
echo "        cargo build --bin ruff && cargo test -p ruff_markdown"
