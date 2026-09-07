"""Read one version's section out of ``docs/CHANGELOG.md``.

The GitHub release for a tag should say the same thing the changelog says. Keeping them in
two places means they disagree, and the one people read (the release page) is the one nobody
remembers to update — so the release notes are EXTRACTED from the changelog rather than
written again:

    python scripts/changelog.py --section 0.0.2      # print that section's body
    python scripts/changelog.py --check 0.0.2        # non-zero if it is missing or empty

A section is a top-level ``## [<version>]`` heading and everything up to the next ``##``.
The heading may carry a date or any other trailing text::

    ## [0.0.2] — 2026-09-07

``--check`` is what the release pipeline runs before publishing anything: a tag with no
changelog section is a release nobody wrote notes for, and that is easier to fix before the
upload than after.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"


def section_pattern(version: str) -> re.Pattern[str]:
    """Return the regex matching ``## [<version>]`` and its body, up to the next heading.

    The version is escaped, so a dotted version cannot act as a wildcard and match a
    neighbouring release (``0.0.2`` must not find ``0x0y2``).
    """
    return re.compile(
        r"^##\s*\[" + re.escape(version) + r"\][^\n]*\n(.*?)(?=^##\s|\Z)",
        re.M | re.S,
    )


def extract(version: str, text: str | None = None) -> str | None:
    """Return the body of ``version``'s changelog section, or ``None`` if there is none.

    Surrounding blank lines are stripped. A heading that exists but has an empty body
    returns the empty string, which :func:`check` treats as missing — a heading on its own
    is not release notes.
    """
    if text is None:
        text = CHANGELOG.read_text(encoding="utf-8")
    found = section_pattern(version).search(text)
    return found.group(1).strip() if found else None


def check(version: str) -> int:
    """Print a diagnosis and return an exit code: 0 when the section is usable."""
    if not CHANGELOG.exists():
        print(f"no changelog at {CHANGELOG}", file=sys.stderr)
        return 1
    body = extract(version)
    if body is None:
        print(
            f"docs/CHANGELOG.md has no '## [{version}]' section.\n"
            f"Add one — rename the [Unreleased] heading to [{version}] and date it — so the "
            "GitHub release has notes somebody wrote.",
            file=sys.stderr,
        )
        return 1
    if not body:
        print(f"'## [{version}]' exists but is empty; write the notes.", file=sys.stderr)
        return 1
    print(f"'## [{version}]' found, {len(body.splitlines())} lines")
    return 0


def main() -> int:
    """Parse arguments and dispatch to --section or --check."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--section", metavar="VERSION", help="print the section body")
    group.add_argument("--check", metavar="VERSION", help="fail if the section is missing")
    args = parser.parse_args()

    if args.check:
        return check(args.check)

    body = extract(args.section)
    if not body:
        print(f"no '## [{args.section}]' section in docs/CHANGELOG.md", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
