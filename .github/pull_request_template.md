<!--
  Keep the title in conventional-commit form, because the release notes are assembled from
  these: feat(p07): …  fix(service): …  docs: …  chore: …
-->

## What changed, and why

<!--
  The *why* is the part worth writing. This project's history is unusually good at explaining
  the reasoning behind a change, and that is what makes a defect from six months ago
  understandable rather than archaeology. If you fixed something, say what the broken
  behaviour looked like from outside — the symptom someone would have reported.
-->

## How it was verified

<!--
  Not "tests pass" — what you actually drove. A real request, a screenshot, the close code you
  observed, the trace you read. Several defects here passed their unit tests for weeks while
  being broken in use, because the test exercised a part nothing had plugged in.
-->

## Checklist

- [ ] `make test` passes locally.
- [ ] `ruff check` and `ruff format --check` are clean.
  <!-- Check the exit code, not the output: piping either into `tail` masks the failure. -->
- [ ] `agentship verify` still exits `0`, with no new over-claims.
- [ ] If an engine capability was declared or changed, its conformance cell proves it.
  <!-- A capability is done when its cell passes, not when a box is ticked. -->
- [ ] Public surface changes are reflected in `docs/capabilities/`.
- [ ] `docs/CHANGELOG.md` has an entry under `## [Unreleased]`.
  <!-- The release workflow publishes this verbatim; a missing section fails the tag. -->
- [ ] A non-obvious design decision has an ADR in `docs/decisions/`.
- [ ] Version numbers untouched — releases are cut by tag, never by hand-editing a version.

## Anything deliberately left undone

<!--
  Known gaps, follow-ups, things that look wrong but are intentional. A limitation stated here
  and marked `xfail` is a known limitation; the same limitation unstated is a bug waiting to be
  rediscovered.
-->

Closes #
