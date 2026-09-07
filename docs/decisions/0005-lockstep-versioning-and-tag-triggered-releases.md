# 0005 — One version for all six packages, released from a tag on `main`

**Status:** accepted · **Scope:** release engineering (all packages); no phase owns this

## Context

AgentShip ships as six distributions — `agentship-core`, `-langgraph`, `-service`,
`-observability`, `-cli` and the `agentship-sdk` meta-package. Six distributions means three
questions that had to be answered before the first tag, and answering them by habit rather than
on purpose is how a project ends up unable to say what version of itself is broken.

**How are they versioned?** Independently, each moving on its own schedule, or in lockstep?
LangGraph — the closest comparable, and a library we already consume — versions independently:
its tags are `cli==0.4.4`, `sdk==0.4.4`, `checkpoint==2.1.1`, with 99 `cli` releases against 3
for `checkpoint-duckdb`. That suits packages with genuinely independent lifecycles, and it buys a
compatibility matrix: every combination of the six is something a user can install, so every
combination is something someone has to reason about.

**What triggers a publish?** A push to `main`, a manual button, or a tag.

**Which branch do releases come from?** Trunk, or a long-lived release branch per line.

The forcing event was concrete. The name-claiming upload of `0.0.1` shipped a build that could
not run an agent at all — observability defaulted to a provider the starter stack does not
install, so the first command in the README failed. PyPI versions are immutable, so that build is
permanent, and the fix has to be a new number. A release process that lets that happen once will
let it happen again.

## Decision

**One version across all six, every sibling pinned to exactly it.** `scripts/versions.py --set`
moves all six and every pin at once; `--check` is a CI gate that fails on any drift. Six
hand-edited strings is how a release ships with one missed.

The six are only ever tested together — the conformance grid, the service contracts and the
engine adapters are exercised as one tree — so a mixed set is a combination nobody has run.
Independent versioning would mean publishing combinations we do not test. We take the cost
(a package with no changes still gets a new number) to keep the guarantee (any two AgentShip
packages a user has installed were tested against each other).

**A tag matching `v*` is the only thing that publishes.** Pushing to a branch runs the test gate
and reaches no index. The release is therefore a deliberate act with a name attached, never a
side effect of merging, and `main` is never one accidental push away from PyPI.

**Releases are cut from `main`. There are no release branches.** A feature branch merges to
`main`, `main` stays releasable, and a tag points at the commit being released. We would only add
a maintenance branch (LangGraph keeps `0.4` and `0.6` this way) to patch an old line *after* main
had moved past it — which cannot arise while there is one supported line. Adding that machinery
now would be guarding a situation we do not have.

**While pre-release, the version is `0.0.x`.** Not `0.1.0`. The phases that define v0.1 are still
in flight, and `0.1.0` is a claim — "usable, still moving" — that the tree cannot make yet.
`0.0.x` says what is true: the packages exist, they are being tested in the open, and no
stability of any kind is promised.

## Consequence

A user who has `agentship-core==0.0.4` knows every other AgentShip package they have is `0.0.4`,
and that the set was tested together. Nobody has to reason about a matrix.

The version on `main` means "the last thing published", not "what this tree is" — those diverge
the moment work continues after a release, and that is expected rather than drift. `--check` only
enforces the six agreeing *with each other*, so ordinary commits never touch a version.

Because a published version can never change, any fix that reaches users needs a new number — but
fixes are batched into a release, not released one at a time. The trigger to cut one is
judgement: routine fixes wait, a build that cannot run does not.

`verify-testpypi` installs the packages back out of a real index and runs the README quickstart
keyless before PyPI is touched. That gate exists because `--help` plus an import passed against
the broken `0.0.1`; the check has to reproduce what a user does, not what a build does.

A future contributor must not: hand-edit a version, publish from a branch, or let the six drift
apart to avoid a "pointless" bump on an unchanged package. The pointless bump is the feature.
