---
name: agentship-debt
description: >-
  Audit what AgentShip claims against what it can prove — the xfail inventory, capabilities
  declared without a passing cell, docs describing code that changed, and specs that drifted
  from the tree. Use when asked about technical debt, "what is unproven", "what are we
  over-claiming", or before a release, a status update, or a paper submission.
---

# Auditing the gap between claim and proof

This project's stated discipline is *declare, don't fake*. Debt here is therefore not
primarily ugly code — it is **the distance between what the project says and what it can
demonstrate**. That distance is what this audit measures.

Run it before: a release, a README or status update, and any paper submission.

## 1. The honest inventory — `xfail`

```bash
make test 2>&1 | grep -iE "^XFAIL"
```

Each is a capability the project does not prove. Read the reason string: a good one states the
limitation and what a correct fix needs. Three standing examples — durable resume after a
kill, the LiveKit adapter never run against a live room, and a sub-agent span parented one
level high.

For each, check: **is the limitation stated everywhere the capability is claimed** — README
status table, capability page, paper, and figures? An `xfail` is honest only if the claim
side matches it.

## 2. Declared but unproven capabilities

```bash
agentship verify --agents-dir <dir>
```

`0 over-claims` means every engine capability has a passing cell. Also check what is **not**
covered: a `SKIPPED` section is honest, but a capability with no cell at all is invisible
here. Compare `EngineCapabilities` fields against the conformance grid, and the
`DEFERRED_CAPABILITIES` map — every field must be covered by a cell or listed there with a
reason.

Voice shipped and ran for weeks with no conformance cell, so `verify` reported nothing about
it either way. Absence of a failure is not proof.

## 3. Built but never wired

The defect this repository produces most. Search for symbols that exist and are never
reached:

- Constants, attributes and close codes defined but never read by a caller
- A function whose only callers are tests
- A config field accepted by the spec and consulted nowhere
- A trace attribute stamped from a map nothing populates

`grep` for each new public name and check the callers are production code. This is cheap and
has repeatedly found real defects.

## 4. Documentation drift

Docs that describe code which has since changed. Check in this order, worst first:

1. **Figures and diagrams** — they rot silently and are the hardest to notice. A container
   diagram here once drew two packages that had never been written.
2. **README status tables and capability pages** — do they still match `verify` and the
   `xfail` list?
3. **Counts** — "six packages" survived two releases after it became seven.
4. **Referenced paths** — every file a README mentions should exist:
   ```bash
   grep -oE '`[a-z_/]+\.(py|yaml|md)`' README.md | tr -d '`' | while read f; do
     [ -e "$f" ] || echo "MISSING: $f"; done
   ```
   A demo README here cited a script and its test seven times after both were deleted.

## 5. Spec drift

Phase files and the status board have drifted repeatedly — claiming not-started work that had
shipped, and listing defects fixed weeks earlier. Before trusting either, reconcile against
the tree: the code, the tests, and `verify`. If a phase file and the board disagree, **neither
is evidence** — the cells are.

## Reporting

Group by **what it costs**, not by file:

- **Claims we cannot support** — fix the claim or the code before shipping or submitting.
  These are urgent because they damage trust the moment someone checks.
- **Proof we owe** — real capability, missing cell. Schedule it.
- **Known limitations, correctly stated** — these are *not* debt. An `xfail` with an accurate
  reason and matching docs is the system working. Say so, so the list is not padded.

Never report a clean audit as a list of worries. If the gap is small, say that plainly and
name what you checked.
