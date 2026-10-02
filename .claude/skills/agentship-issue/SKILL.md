---
name: agentship-issue
description: >-
  File an AgentShip issue that is actionable on arrival — the right form, the evidence to
  gather first, and the labels. Use when asked to "file an issue", "open a bug", "track
  this", or when a problem is found that will not be fixed in the current change.
---

# Filing an issue

## Gather the evidence before writing anything

Two commands answer most of what a maintainer would otherwise have to ask for, and both are
in the bug form because of how often that round trip happened:

```bash
agentship doctor --agents-dir <dir>    # can it run HERE — missing SDK, unset key
agentship verify --agents-dir <dir>    # is the spec valid, are the engines honest
```

They answer different questions and the distinction matters: `doctor` red with `verify` green
means the environment, not the code. If `verify` is red, the problem is usually upstream of
whatever was being attempted.

Also capture the version (`pip show agentship-core`) and how it was installed. The packages
ship in lockstep, so one version describes them all — **if they disagree, that mismatch is
itself the bug** and should lead the report.

## Pick the form

| Form | For |
|---|---|
| `bug_report.yml` | Behaves differently from what it says it does |
| `feature_request.yml` | Something it should be able to do and cannot |
| `documentation.yml` | Docs wrong, missing, or describing code that has changed |

Blank issues are disabled, so use a form. Docs drift is filed as a **bug**, not a chore — it
is the failure this project hits most often, and demoting it is how it keeps happening.

A question, or something not yet known to be a bug, belongs in **Discussions**, not Issues. It
can be promoted later if it turns out to be real.

## Write it like a person, not a template

This is the part that goes wrong most. An issue is one human telling another what is broken —
so write it as prose, in the voice you would use explaining it out loud.

What that rules out:

- **Headings on a four-line issue.** "## Problem Statement / ## Impact / ## Acceptance
  Criteria" on something you could say in a paragraph is ceremony, and it buries the point.
- **Tables where sentences would do.** A table earns its place when there are genuinely
  parallel items to compare. Three bullets of prose usually beat it.
- **Corporate hedging.** "It has been observed that the system may exhibit…" — say "pressing
  the mic times out".
- **Restating the title in the first line.** Start with what actually happened.

What to keep: specifics. Numbers, exact error text, the command you ran. Plain language is not
vague language — "the container crash-looped and `make ui` opened a dead tab" is both human
and precise.

A `## Done when` checklist at the end is worth having, because it is the one part a reader
scans later to see whether the issue is finished.

**Title: the symptom, not the theory.** "Voice fails with a timeout when the agent has no
voice block" — not "accept() called in wrong order". The theory is often wrong; the symptom is
what someone else will search for. Put the theory in the body.

Reproduce with the smallest spec plus a command. Specs here are small enough to paste whole,
and a spec beats a description because it removes the guessing.

If something was already tried and ruled out, say so. It saves the same dead end being walked
twice.

## Label it

Three axes, applied together:

- `type:` — bug · feature · docs · chore (the forms apply this)
- `area:` — core · langgraph · voice · service · observability · cli · demo
- `status:` — the forms apply `status:triage`; leave it until triaged

Two labels carry real meaning and should not be used loosely:

- **`unproven`** — a capability that is built but not proven end to end. This is the `xfail`
  list written down. It does **not** block a release; it blocks *claiming* the capability in
  the README, the docs, or the paper. Use it only where the code genuinely ships and the proof
  does not exist.
- **`status:blocked`** — must name what it is waiting on, in a comment. A blocked issue with
  no stated blocker is how work quietly stops.

## Milestones

Milestones are **goals** (`v1`, `v2`), not release scopes. Attach one only if the issue is
genuinely part of that goal. Leaving it unmilestoned means "not scheduled", which is an honest
and common state — far better than attaching a milestone that will be bumped three times.

Releases are cut on cadence and carry whatever is finished, so an issue never needs
re-milestoning because a release went out without it.
