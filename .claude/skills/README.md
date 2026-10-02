# Project skills

Six skills that encode how this project is worked on. They load automatically for anyone using
Claude Code in this repository — no setup, because they are versioned alongside the code they
describe.

| Skill | Use it when |
|---|---|
| [`agentship-pr`](agentship-pr/SKILL.md) | Opening a pull request |
| [`agentship-issue`](agentship-issue/SKILL.md) | Filing a bug, feature or docs issue |
| [`agentship-review`](agentship-review/SKILL.md) | Reviewing a change, adversarially |
| [`agentship-debt`](agentship-debt/SKILL.md) | Auditing claims against proof |
| [`agentship-release`](agentship-release/SKILL.md) | Cutting a release |
| [`agentship-spec`](agentship-spec/SKILL.md) | Phases, the status board, closing a phase |

## Why these exist

They are not style guides. Each one encodes a mistake this project actually made, so it is
made once rather than repeatedly:

- **`agentship-review`** hunts one defect above all others — code that is built, tested, and
  wired to nothing. That shape appeared six times in a single week, and every instance had a
  passing test over a part no caller invoked.
- **`agentship-pr`** says to check the gate's *exit codes*, because piping `ruff` into `tail`
  returns tail's status and a formatting failure was committed while the gate reported clean.
- **`agentship-release`** front-loads the trusted-publisher check, because a first-time package
  that is not registered on both indexes half-publishes, and a published version number can
  never be reused.
- **`agentship-debt`** and **`agentship-spec`** both exist because the status board has
  claimed work was not started while it was published, and listed defects fixed weeks earlier.

The thread running through all six: **a passing test is not evidence that a feature is
reachable, and a ticked box is not evidence that it works.** The conformance cells are.

## Adding one

A skill earns its place by encoding something that went wrong, or a rule a newcomer would
otherwise have to learn by breaking. Keep it short, make it concrete, and cite the real
incident — the specifics are what make a rule memorable and what let a reader judge whether it
still applies.
