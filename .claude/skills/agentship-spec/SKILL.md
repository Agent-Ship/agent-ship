---
name: agentship-spec
description: >-
  Work with AgentShip's phase specs and status board — reconciling a phase against the tree
  before calling anything done, recording deviations, and closing a phase. Use when asked
  about phases, "what is done", updating STATUS, or finishing a phase.
---

# Phases, specs, and what "done" means

The specs live in `.spec-dev/` — phase files plus `STATUS.md`, the board. Two facts about them
shape everything below:

- **`STATUS.md` is authoritative over phase-file checkboxes.** The checkboxes are historical
  scratch and have been wrong more often than right.
- **Both have drifted badly.** The board once said voice was "not started" while the package
  was published on PyPI, and listed four service defects when three had been fixed weeks
  earlier.

So neither document is evidence. The tree is.

## Before marking anything done

Reconcile against reality, in this order:

1. **Does the code exist?** Find the module, the function, the route.
2. **Is it reachable?** Who calls it in production code? A symbol whose only caller is a test
   is the defect this repo produces most — the interrupt marker, the MCP server attribute and
   the stage timers were all "done" and unreachable.
3. **Does a conformance cell prove it?** This is the real bar. **A capability is done when its
   cell passes**, not when a box is ticked. Voice shipped and ran for weeks with no cell, so
   `agentship verify` reported nothing about it either way.
4. **Is it honest everywhere it is claimed?** README status table, capability page, paper,
   figures.

Only then update the board — and cite the evidence in the entry: the commit, the test, the
measured number.

## Recording a deviation

Implementations routinely differ from what a spec described, and that is fine. What is not
fine is silently ticking the box as though they matched, because the next reader cannot tell
which parts of the spec still describe the system.

Write the deviation down with its reason, in the phase file:

> **D2 · Voice invokes the agent in-process; the service hosts voice.** The spec had a voice
> runtime calling back into the REST API over WS. Shipped: the service *is* the voice host…
> This removes a network hop from the latency path and a second configuration surface.

A deviation with a stated reason stops being drift and becomes a decision. If it is a big one,
it also wants an ADR in `docs/decisions/`.

## Closing a phase

The Definition-of-Done gate is three documents plus the proof:

- the conformance cells pass,
- `docs/capabilities/<phase>.md` exists and is accurate,
- an ADR for any non-obvious decision,
- a `docs/CHANGELOG.md` entry,
- and the phase file's checkboxes reconciled to reality, deviations recorded.

Dependencies on unstarted phases are **not** gaps. If guardrail stages wait on a guardrails
phase that has not begun, say so and close the phase without them — leaving it open forever
makes the board useless.

## Known trap

`.spec-dev/` is **not in any git repository**. 65 documents, no history, no review, no backup,
and invisible to anyone who clones the project. Any edit there is unversioned. Flag this when
it comes up; the fix is to move the directory into the repo.
