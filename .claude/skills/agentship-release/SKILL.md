---
name: agentship-release
description: >-
  Cut an AgentShip release — the lockstep version bump, the notes-before-tag rule, the
  trusted-publisher step that only a human can do, and what cannot be undone. Use when asked
  to "cut a release", "ship 0.0.x", "tag a version", or to prepare one.
---

# Cutting a release

Seven packages ship in **lockstep**: one version across all of them, siblings pinned exactly.
A mixed set is a combination nobody has tested, so the tooling refuses to produce one.

The release is **tag-triggered**. Pushing `v*` builds every package, publishes to TestPyPI,
installs it back out to prove it works, then publishes to PyPI and writes the GitHub release.

## What cannot be undone

**A published version is permanent.** PyPI does not allow replacing a file, and yanking hides
a release without freeing the number. So:

- A bad release costs a version number, never a repair.
- A half-published release — some packages up, one failing — cannot be completed. The
  remaining packages must go out under the *next* number.

That asymmetry is why the checks below run before the tag, not after.

## Before tagging

1. **Notes first.** Rename `## [Unreleased]` in `docs/CHANGELOG.md` to `## [<version>] — <date>`.
   The workflow publishes that section verbatim as the release notes, and **a tag with no
   matching section fails the release**. Confirm with:
   ```bash
   python scripts/changelog.py --check <version>
   ```
2. **Bump in lockstep and verify:**
   ```bash
   python scripts/versions.py --set <version>
   python scripts/versions.py --check     # all seven, every sibling pinned
   ```
3. **Run the full gate** — `make test`, `ruff check`, `ruff format --check`,
   `mkdocs build --strict`, `agentship verify`. Check exit codes, not output.
4. **Build and inspect the artifacts** — every wheel builds, `twine check` passes on each,
   and installing them together into a clean venv gives the expected version.
5. **A NEW package needs a trusted publisher first.** This is the step that bites. A package
   being published for the first time must have a pending trusted publisher registered on
   **both** test.pypi.org and pypi.org before the tag, or the release half-publishes and the
   number is burnt. Check:
   ```bash
   curl -s -o /dev/null -w "%{http_code}" https://pypi.org/pypi/<package>/json   # 404 = new
   ```
   Registration is a form on each index — owner, repo, workflow file, environment name — and
   **only the maintainer can do it**. Ask, and wait for confirmation.

## Tagging

Pushing to `main` is blocked for the assistant, so the maintainer runs the tag. Give them the
exact command rather than a description:

```bash
git fetch origin && git tag v<version> origin/main && git push origin v<version>
```

Tag `origin/main`, not local `main` — local main diverges after squash merges, and tagging it
ships the wrong tree.

## After

Watch the workflow through all four stages. If the install-back-out step fails, the release
stopped before PyPI and the number is still usable — fix and re-tag. If PyPI publishing has
begun, it is not.

Then update anything pinned to the old version — the demo's requirements, any install
instructions quoting a version.

## When to cut at all

Cadence, not scope. Batch routine fixes and features; ship immediately only when the published
build is unusable. Releases are steps toward a milestone (`v1`, `v2`), and never wait for one
to empty — the milestone is the goal, the release is a step.
