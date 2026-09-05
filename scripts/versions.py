"""Keep the six package versions, and the pins between them, in lockstep.

These packages are released together and only ever tested together. Nothing stops them
drifting apart, and two things go wrong when they do:

* a user installs ``agentship==0.2.0`` and resolves ``agentship-core==0.9.0``, because
  the meta-package depended on its siblings with no version constraint at all — a
  combination nobody has ever run;
* six hand-edited version strings mean a release where one was missed, which is a broken
  release that looks fine until someone installs it.

So: one version across all six, every sibling dependency pinned to exactly it.

    python scripts/versions.py --check        # CI gate; non-zero on any drift
    python scripts/versions.py --set 0.1.0    # bump everything at once

Deliberately a script over a dynamic-version build hook: the version is greppable in each
pyproject.toml, where a reader expects it, and the invariant is enforced by something you
can run and read rather than by build-backend magic.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "packages"

#: The distributions released in lockstep. A name here must have a packages/<name>/.
SIBLINGS = (
    "agentship",
    "agentship-core",
    "agentship-langgraph",
    "agentship-service",
    "agentship-observability",
    "agentship-cli",
)

_VERSION_LINE = re.compile(r'^version = "([^"]+)"$', re.M)
#: A sibling dependency, with or without extras and with or without an existing pin:
#: "agentship-core", "agentship-core==0.1.0", "agentship-service[serve]==0.1.0".
_SIBLING_DEP = re.compile(
    r'"(' + "|".join(SIBLINGS) + r')(\[[a-z0-9,_-]+\])?(==[^"]+)?"',
)


def pyprojects() -> list[Path]:
    """Every package's pyproject.toml, in a stable order."""
    return [PACKAGES / name / "pyproject.toml" for name in SIBLINGS]


def declared_version(path: Path) -> str:
    """The version a pyproject.toml declares."""
    match = _VERSION_LINE.search(path.read_text())
    if not match:
        raise SystemExit(f'{path.relative_to(ROOT)}: no `version = "..."` line')
    return match.group(1)


def sibling_pins(path: Path) -> list[tuple[str, str | None]]:
    """Every sibling this package depends on, as ``(name, pin)`` with ``None`` for unpinned.

    Skips the package's own name, which appears in its ``[project]`` table and in entry
    points, and is not a dependency on itself.
    """
    own = path.parent.name
    found = []
    for name, _extras, pin in _SIBLING_DEP.findall(path.read_text()):
        if name != own:
            found.append((name, pin[2:] if pin else None))
    return found


def check() -> int:
    """Report any version drift or unpinned sibling. Returns a process exit code."""
    problems: list[str] = []
    versions = {path.parent.name: declared_version(path) for path in pyprojects()}

    if len(set(versions.values())) != 1:
        problems.append("the six versions are not identical:")
        problems += [f"    {name:24} {version}" for name, version in sorted(versions.items())]

    expected = next(iter(versions.values()))
    for path in pyprojects():
        for name, pin in sibling_pins(path):
            where = path.relative_to(ROOT)
            if pin is None:
                problems.append(f"{where}: depends on {name} with no version pin")
            elif pin != expected:
                problems.append(f"{where}: pins {name}=={pin}, expected =={expected}")

    if problems:
        print("Version drift:\n", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print("\nRun: python scripts/versions.py --set <version>", file=sys.stderr)
        return 1

    print(f"All {len(versions)} packages at {expected}, every sibling pinned to it.")
    return 0


def set_version(version: str) -> int:
    """Set every package's version, and every sibling pin, to ``version``."""
    if not re.fullmatch(r"\d+\.\d+\.\d+([abrc]\w*|\.dev\d+)?", version):
        raise SystemExit(f"{version!r} is not a PEP 440 release version, e.g. 0.1.0 or 0.1.0rc1")

    for path in pyprojects():
        text = path.read_text()
        text = _VERSION_LINE.sub(f'version = "{version}"', text, count=1)

        def repin(match: re.Match[str], own: str = path.parent.name) -> str:
            """Pin a sibling dependency to ``version``, preserving any extras.

            ``own`` is bound as a default argument rather than closed over: closing over
            the loop variable would make every call use the LAST package's name once the
            loop moved on, silently rewriting each package's own name into a
            self-dependency.
            """
            name, extras = match.group(1), match.group(2) or ""
            if name == own:
                return match.group(0)  # its own name, not a dependency
            return f'"{name}{extras}=={version}"'

        # Only the dependency tables: an entry point like "agentship.engines" must not be
        # rewritten, and the [project] name line must keep its bare name.
        text = _SIBLING_DEP.sub(repin, text)
        path.write_text(text)

    print(f"Set {len(SIBLINGS)} packages to {version}.")
    return check()


def main() -> int:
    """Parse arguments and run the requested action."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="fail if versions or pins drifted")
    group.add_argument("--set", metavar="VERSION", help="set every version and pin")
    args = parser.parse_args()
    return check() if args.check else set_version(args.set)


if __name__ == "__main__":
    raise SystemExit(main())
