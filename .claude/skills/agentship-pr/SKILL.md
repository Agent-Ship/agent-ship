---
name: agentship-pr
description: >-
  Open a pull request against AgentShip — the gate to run, what the commit message and PR
  body must explain, and the branch rules. Use when asked to "open a PR", "raise a PR",
  "push this as a PR", or when finishing a change that is ready to land.
---

# Opening a pull request

## Branch first

`main` is protected and pushing to it directly is blocked. **Branch before committing** —
`feat/`, `fix/`, `chore/`, `docs/`, `refactor/`.

Two traps that have cost real time here:

- **`git branch X` creates without switching.** Commits then land on the branch you were
  already on, and the push is a silent no-op. Use `git switch -c`.
- **PRs are squash-merged**, so after a merge your local branch's commits are no longer
  ancestors of `main`. A later `pull --rebase` tries to replay work that is already upstream
  and conflicts. Branch fresh from `origin/main` instead of rebasing a merged branch.

## The gate

Run all four, and **check exit codes, not output**:

```bash
make test                              # the suite
ruff check .                           # lint
ruff format --check .                  # formatting
agentship verify --agents-dir <dir>    # no over-claims
```

Piping any of these into `tail` or `grep` makes the pipeline return *that* command's status,
not ruff's. A formatting failure was committed here while the gate reported clean, for exactly
that reason. Run them bare, or check `$?` explicitly.

Docs changes additionally need `mkdocs build --strict` — it fails on a dead internal link.

## What the commit message has to do

Explain **why**, and what the broken behaviour looked like from outside. The history here is
the project's best asset for understanding a decision months later; a message that only
restates the diff wastes it.

For a fix, lead with the symptom a user would have reported:

> *"Voice failed: timed out opening the voice socket."*
>
> The route closed before calling `accept()`. That is not a WebSocket close — Starlette
> answers the handshake with a bare HTTP 403 and discards the code…

Conventional commit titles, because release notes are assembled from them: `feat(p07):`,
`fix(service):`, `docs:`, `chore:`.

**Never add `Co-Authored-By` or "Generated with" trailers.**

## What the PR body has to do

The template asks for three things. The second is the one people skip and the one that matters:

- **What changed, and why.**
- **How it was verified** — not "tests pass". What you *drove*: the request you made, the close
  code you observed, the span tree you printed, the screenshot you took. Several defects here
  held passing tests for weeks while broken in use.
- **Anything deliberately left undone** — known gaps and follow-ups. A limitation stated and
  marked `xfail` is a known limitation; the same limitation unstated is a bug waiting to be
  rediscovered.

**Link the issue by name, not just number.** `Closes #52 — *Nothing in the repo says how to
file a bug, get help, or open a PR*`. A bare `Closes #52` means nothing in a notification, a
changelog, or a list of merged PRs six months from now — and naming it also catches the case
where you linked the wrong issue.

**The link only works if the PR targets the default branch.** `Closes #53` on a PR based on
another branch — a stacked PR — is treated as plain text and silently ignored. No error, no
warning, and the issue shows no link. If you stack a PR, either base it on `main` anyway and
let the diff narrow when the parent merges, or retarget it before asking anyone to look.

Confirm rather than assume the keyword took:

```bash
gh api graphql -f query='{repository(owner:"Agent-Ship",name:"agent-ship"){
  pullRequest(number:NN){closingIssuesReferences(first:5){nodes{number title}}}}}'
```

Write the body the way you would explain the change to someone, not as a filled-in form. Same
rule as issues: prose over headings, specifics over hedging.

Add the `## [Unreleased]` changelog entry. That section *is* the next release's scope, so a
missing entry means the work ships unannounced — and a tag with no matching section fails the
release workflow outright.

## After opening

Watch CI to completion (`gh pr checks <n> --watch`) and report the result honestly. Do not
describe a PR as ready while checks are pending or red.

Do not merge. Merging is the maintainer's call.
