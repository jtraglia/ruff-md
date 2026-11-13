#!/usr/bin/env python3
"""Let markdown files through `ruff check`'s source-type filter, in place.

Called by fork/apply.sh. This one line used to live in fork/fork.patch and was
the only part of it that ever broke: applied forward across the nine releases
from 0.15.14 to 0.16.0, the patch's other hunks survived every one, while this
hunk started rejecting at 0.15.22.

The reason is what it anchors to. `check.rs` lists the source types ruff will
lint, and upstream extends that list whenever ruff learns a new file type —
`rule-codes-in-selectors` (#26772) added `TomlSourceType::Ruff` to it, which
changed the exact lines a patch hunk needs to match. Nothing about that list is
going to stop growing.

So instead of matching the list's text, this finds the `matches!` block and
appends to whatever arm it currently has. Verified against all nine releases
0.15.15-0.16.0: the anchor resolves in every one, including the two where the
patch hunk failed.

It is idempotent — if markdown is already accepted, whether because this ran
twice or because upstream added it themselves, it says so and changes nothing.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# The `paths.retain(..)` filter in `check`. Matched structurally: the capture
# takes the whole match arm, whatever upstream currently has in it, and the
# trailing `)` + `}` pins this to the retain closure rather than any other
# `matches!` in the file.
FILTER_RE = re.compile(
    r"(matches!\(\s*SourceType::from\(path\),\s*)(?P<arm>.+?)(\s*\)\s*\})", re.S
)

WANTED = "SourceType::Markdown"


def main() -> int:
    path = Path(
        sys.argv[1] if len(sys.argv) > 1 else "crates/ruff/src/commands/check.rs"
    )
    text = path.read_text(encoding="utf-8")

    match = FILTER_RE.search(text)
    if match is None:
        print(
            "accept_markdown.py: could not find the "
            "`matches!(SourceType::from(path), ..)` filter in "
            f"{path}. Upstream has restructured it; the fork needs updating.",
            file=sys.stderr,
        )
        return 1

    arm = match.group("arm")
    if WANTED in arm:
        print(f"    {path.name}: markdown already accepted")
        return 0

    # Continue the arm on its own line, indented one level past the arm's first
    # line, which is what rustfmt produces for a multi-line `|` chain. The arm's
    # own indentation was consumed into group 1, so take it from there.
    prefix = match.group(1)
    line_start = prefix[prefix.rfind("\n") + 1 :]
    base_indent = line_start if line_start.strip() == "" else ""
    new_arm = f"{arm}\n{base_indent}    | {WANTED}"

    path.write_text(
        text[: match.start()]
        + match.group(1)
        + new_arm
        + match.group(3)
        + text[match.end() :],
        encoding="utf-8",
    )
    print(f"    {path.name}: added `| {WANTED}`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
