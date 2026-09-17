#!/usr/bin/env python3
"""Rewrite upstream's .github/workflows/release.yml for this fork, in place.

Called by fork/apply.sh. This is NOT a frozen copy of release.yml — that file
belongs to upstream, it changes, and it calls other upstream workflows
(build-binaries.yml, publish-pypi.yml) whose inputs change with it. Freezing it
would mean a stale copy passing stale inputs to workflows that had moved on.

So the fork's three changes are re-derived from whatever upstream ships:

  1. Depot runners -> ubuntu-latest. Depot is only registered to the upstream
     organisation, so its runners never come up in a fork.
  2. Every job outside KEEP_JOBS gets `if: false`. This is an allowlist, not a
     list of jobs to disable, so a job upstream adds later is off by default
     rather than silently running against the fork. That is not hypothetical:
     0.16.0 added `custom-publish-crates`, which would otherwise have tried to
     publish to crates.io.
  3. The PyPI publish job is force-run, because upstream's condition depends on
     `dist` plan output that evaluates to a silent skip here.

None of these read the *current* value of what they overwrite, so upstream
editing a runner label or an `if:` expression cannot break them. What they do
depend on is release.yml's structure: a top-level `jobs:` key with job names at
two-space indent. If a job in KEEP_JOBS goes missing, that means upstream
renamed or removed part of the release pipeline, and this exits non-zero rather
than quietly producing a release that skips publishing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Jobs the fork needs in order to build binaries and publish to PyPI.
KEEP_JOBS = {
    "release-gate",
    "plan",
    "custom-build-binaries",
    "build-global-artifacts",
    "host",
    "custom-publish-pypi",
}

PYPI_JOB = "custom-publish-pypi"
PYPI_IF = "${{ always() && needs.host.result == 'success' }}"

JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ".github/workflows/release.yml")
    text = path.read_text(encoding="utf-8")

    # 1. Depot runners. Matches any depot-* label, quoted or not.
    text, runners = re.subn(
        r'^(\s*runs-on:\s*)"?depot-[a-z0-9.-]+"?\s*$',
        r"\1ubuntu-latest",
        text,
        flags=re.M,
    )

    lines = text.splitlines(keepends=True)
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == "jobs:")
    except StopIteration:
        print("release_workflow.py: no top-level `jobs:` key", file=sys.stderr)
        return 1

    out = lines[: start + 1]
    index = start + 1
    seen: list[str] = []
    disabled: list[str] = []

    while index < len(lines):
        match = JOB_RE.match(lines[index])
        if match is None:
            out.append(lines[index])
            index += 1
            continue

        job = match.group(1)
        seen.append(job)
        out.append(lines[index])
        index += 1

        if job in KEEP_JOBS and job != PYPI_JOB:
            continue

        # Consume this job's body, dropping any job-level `if:` and its
        # continuation lines. Step-level `if:` are indented deeper and survive.
        body: list[str] = []
        while index < len(lines) and not JOB_RE.match(lines[index]):
            if re.match(r"^    if:", lines[index]):
                index += 1
                while index < len(lines) and re.match(r"^     +\S", lines[index]):
                    index += 1
                continue
            body.append(lines[index])
            index += 1

        if job == PYPI_JOB:
            out.append(
                "    # ruff-md: force-run; upstream's condition silently skips here.\n"
            )
            out.append(f"    if: {PYPI_IF}\n")
        else:
            out.append("    # ruff-md: not published by this fork.\n")
            out.append("    if: false\n")
            disabled.append(job)
        out.extend(body)

    missing = sorted(KEEP_JOBS - set(seen))
    if missing:
        print(
            "release_workflow.py: expected job(s) missing from upstream "
            f"release.yml: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    path.write_text("".join(out), encoding="utf-8")
    print(f"    {runners} depot runner(s) -> ubuntu-latest")
    print(f"    {PYPI_JOB}: force-run")
    print(f"    disabled {len(disabled)} job(s): {', '.join(disabled)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
