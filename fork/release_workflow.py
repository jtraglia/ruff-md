#!/usr/bin/env python3
"""Rewrite upstream's .github/workflows/release.yml for this fork, in place.

Called by fork/apply.sh. This is NOT a frozen copy of release.yml — that file
belongs to upstream, it changes, and it calls other upstream workflows
(build-binaries.yml, publish-pypi.yml) whose inputs change with it. Freezing it
would mean a stale copy passing stale inputs to workflows that had moved on.

So the fork's four changes are re-derived from whatever upstream ships:

  1. Depot runners -> ubuntu-latest. Depot is only registered to the upstream
     organisation, so its runners never come up in a fork.
  2. Every job outside KEEP_JOBS gets `if: false`. This is an allowlist, not a
     list of jobs to disable, so a job upstream adds later is off by default
     rather than silently running against the fork. That is not hypothetical:
     0.16.0 added `custom-publish-crates`, which would otherwise have tried to
     publish to crates.io.
  3. The PyPI publish job is force-run, because upstream's condition depends on
     `dist` plan output that evaluates to a silent skip here.
  4. Jobs in REPLACED_JOBS are swapped for a fork-owned stand-in. 0.17.0 added
     `sign-release-binaries`, which signs with Astral's certificates, and made
     the jobs after it consume its outputs instead of the raw build artifacts.
     Disabling it would skip those jobs, and keeping it cannot work here.

None of these read the *current* value of what they overwrite, so upstream
editing a runner label or an `if:` expression cannot break them. What they do
depend on is release.yml's structure: a top-level `jobs:` key with job names at
two-space indent. If a job in KEEP_JOBS goes missing, that means upstream
renamed or removed part of the release pipeline, and this exits non-zero rather
than quietly producing a release that skips publishing.

The same goes for a kept job whose `if:` requires `needs.<job>.result ==
'success'`, without also accepting 'skipped', of a job that is disabled: GitHub
skips it, and everything after it, without an error. 0.16.3 added
`generate-checksum-manifest`, which `host` then required, and every release
until 0.16.9 skipped the PyPI publish that way. Likewise, if release.yml reads an
output of a replaced job that its stand-in does not provide, this exits non-zero.
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
    "generate-checksum-manifest",
    "build-global-artifacts",
    "host",
    "custom-publish-pypi",
}

# Stand-in for upstream's signing job: the same outputs, holding the unsigned
# build artifacts. `pypi-artifacts` is what 0.16.9's publish-pypi.yml uploaded
# (`wheels-*`), and `github-archives` overlays the archives `artifacts-*`
# already provides, so the jobs reading them publish what they did before
# signing existed. Neither upload name may match those two download patterns.
SIGN_STAND_IN = """\
    # ruff-md: upstream signs with Astral's certificates, which this fork does
    # not have. Pass the unsigned build artifacts through under the same outputs.
    needs:
      - custom-build-binaries
    permissions:
      contents: read
    runs-on: ubuntu-24.04
    outputs:
      pypi-artifacts: ${{ steps.pypi.outputs.artifact-id }}
      github-archives: ${{ steps.archives.outputs.artifact-id }}
    steps:
      - name: "Download wheels and source distribution"
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          pattern: wheels-*
          path: pypi
          merge-multiple: true
      - name: "Download GitHub release archives"
        uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1
        with:
          pattern: artifacts-*
          path: github-archives
          merge-multiple: true
      - name: "Upload PyPI distributions"
        id: pypi
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: release-pypi-${{ github.run_id }}-${{ github.run_attempt }}
          path: pypi
          if-no-files-found: error
      - name: "Upload GitHub release archives"
        id: archives
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: unsigned-github-archives-${{ github.run_id }}-${{ github.run_attempt }}
          path: github-archives
          if-no-files-found: error
"""

# Job name -> (replacement body, outputs the replacement provides).
REPLACED_JOBS = {
    "sign-release-binaries": (SIGN_STAND_IN, {"pypi-artifacts", "github-archives"}),
}

PYPI_JOB = "custom-publish-pypi"
PYPI_IF = "${{ always() && needs.host.result == 'success' }}"

JOB_RE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
RESULT_RE = re.compile(r"needs\.([A-Za-z0-9_-]+)\.result == '(success|skipped)'")
OUTPUT_RE = re.compile(r"needs\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)")


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ".github/workflows/release.yml")
    text = path.read_text(encoding="utf-8")

    # 1. Depot runners. Matches any depot-* label, quoted or not.
    text, runners = re.subn(
        r'^(\s*runs-on:\s*)"?depot-[a-z0-9.-]+"?\s*$',
        r"\1ubuntu-latest",
        text,
        flags=re.MULTILINE,
    )

    lines = text.splitlines(keepends=True)
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip() == "jobs:")
    except StopIteration:
        print("release_workflow.py: no top-level `jobs:` key", file=sys.stderr)
        return 1

    out = lines[: start + 1]
    index = start + 1
    body: list[str]
    seen: list[str] = []
    disabled: list[str] = []
    replaced: list[str] = []
    # Kept jobs mapped to the jobs their `if:` requires to have succeeded.
    required: dict[str, set[str]] = {}

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

        if job in REPLACED_JOBS:
            body = []
            while index < len(lines) and not JOB_RE.match(lines[index]):
                body.append(lines[index])
                index += 1
            # Blank lines and comments before the next job belong to that job.
            trailing = 0
            while trailing < len(body) and re.match(
                r"^(\s*|  #.*)$", body[-1 - trailing].rstrip("\n")
            ):
                trailing += 1
            out.append(REPLACED_JOBS[job][0])
            out.extend(body[len(body) - trailing :])
            replaced.append(job)
            continue

        if job in KEEP_JOBS and job != PYPI_JOB:
            condition: list[str] = []
            while index < len(lines) and not JOB_RE.match(lines[index]):
                if re.match(r"^    if:", lines[index]):
                    condition.append(lines[index])
                    for continuation in lines[index + 1 :]:
                        if not re.match(r"^     +\S", continuation):
                            break
                        condition.append(continuation)
                out.append(lines[index])
                index += 1
            # A job the condition also accepts as 'skipped' (upstream's
            # optional docker and wasm builds) is fine to disable.
            results = RESULT_RE.findall("".join(condition))
            required[job] = {dep for dep, result in results if result == "success"} - {
                dep for dep, result in results if result == "skipped"
            }
            continue

        # Consume this job's body, dropping any job-level `if:` and its
        # continuation lines. Step-level `if:` are indented deeper and survive.
        body = []
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

    broken = sorted(
        f"{job} requires {dep}"
        for job, deps in required.items()
        for dep in deps
        if dep in disabled
    )
    if broken:
        print(
            "release_workflow.py: kept job(s) require a disabled job to "
            f"succeed, so they would be skipped: {'; '.join(broken)}. "
            "Add the required job to KEEP_JOBS.",
            file=sys.stderr,
        )
        return 1

    unprovided = sorted(
        {
            f"{job}.outputs.{output}"
            for job, output in OUTPUT_RE.findall(text)
            if job in REPLACED_JOBS and output not in REPLACED_JOBS[job][1]
        }
    )
    if unprovided:
        print(
            "release_workflow.py: release.yml reads output(s) the fork's "
            f"stand-in jobs do not provide: {', '.join(unprovided)}",
            file=sys.stderr,
        )
        return 1

    path.write_text("".join(out), encoding="utf-8")
    print(f"    {runners} depot runner(s) -> ubuntu-latest")
    print(f"    {PYPI_JOB}: force-run")
    for job in replaced:
        print(f"    {job}: replaced with the fork's stand-in")
    print(f"    disabled {len(disabled)} job(s): {', '.join(disabled)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
