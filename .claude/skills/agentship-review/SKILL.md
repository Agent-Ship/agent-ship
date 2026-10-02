---
name: agentship-review
description: >-
  Adversarial code review for AgentShip changes, tuned to the defects this repository
  actually produces — code that is built, tested, and never wired to anything. Use when
  reviewing a PR or a diff, when asked to "review", "check this change", "do an adversarial
  review", or before marking a capability done.
---

# Reviewing an AgentShip change

The goal is not to find style problems. It is to find the **one failure mode this repository
keeps producing**, and then the ordinary correctness ones.

## The house defect: built, tested, never wired

Six times in a single week, a feature here was written, given a passing test, and connected to
nothing. Each was invisible to the suite because the test exercised a part no caller invoked:

| What shipped | What was wrong |
|---|---|
| `[cancelled by user]` marker | `transcript()` built the right string; **nothing ever called it** |
| `agentship.tool.mcp_server` | Stamped from a map that **no caller ever populated** |
| `asr_ms` / `tts_ms` / `total_ms` | Declared on the trace and **never assigned** |
| Studio agent badges | Read **engine** capabilities, so all nine agents looked identical |
| Voice close codes 4403/4404/4503 | Sent **before `accept()`**, so a real browser could never receive them |
| The greeting | Queued on `Pipeline`, which has no `queue_frames` — the **test stub had the method** |

So the first question on any review is not "is this correct" but:

> **Who calls this, and which test proves the call happens?**

Ask it of every new function, attribute, constant, close code and config field in the diff. If
the only caller is a test, that is the finding. Write it up even when the code is otherwise
perfect — especially then, because perfect unreachable code is what passes review.

### The stub tell

When a test uses a hand-written double, check the double against the **real** class:

- Does the real object have the method the stub has? (`Pipeline` did not have `queue_frames`.)
- Does the real object deliver the value the stub returns?
- Would this test still pass if the production call site were deleted?

A double that agrees with the author's assumption tests the assumption, not the code. In this
repo the rule is: **fake the provider, never our own code** — stand-ins subclass the real
vendor class (`STTService`, `TTSService`) so the pipeline, frames and seams are genuine.

## Then the ordinary passes

Work through these, in order, reporting only what you can defend with a concrete failure:

1. **Spec vs environment.** Does this treat a missing SDK or unset key as an invalid spec?
   Those are different: a name that does not exist is wrong everywhere; a missing key is wrong
   on one machine. Conflating them broke `verify` and took down a whole service.
2. **Failure scoping.** Can one agent's problem take down the others? One unusable voice agent
   once crash-looped a service of ten.
3. **Error delivery.** Does the failure reach the caller with a reason they can act on? A close
   code sent before `accept()`, a bare `1011`, or an exception swallowed into a timeout are all
   the same bug: the server knew and did not say.
4. **Honesty of claims.** Does a docstring, README line, capability page or figure now claim
   something the code does not do? Is a capability declared whose conformance cell does not
   prove it?
5. **Tenancy and identity.** Is anything keyed on a client-supplied id alone? Conversation
   state must key on `ctx.conversation_key` (tenant + agent + session), never `session_id`.
6. **Concurrency.** Shared mutable state on an agent or engine reused across turns.
7. **Fail-open where it matters.** Tracing, history correction, and telemetry must never break
   a turn. Conversely, auth and tenancy must never fail open.

## Verifying a finding before reporting it

Do not report a suspicion. Each finding needs a concrete failure: inputs or state → wrong
output, with the code path named. If you cannot construct one, say so and mark it a question
rather than a defect.

Where cheap, **run it**: drive the endpoint, open the socket, print the span tree. Several
"bugs" found by reading in this repo turned out to be fine, and one that read fine
(`research-team` answering in 1.7s) turned out to be correct only after the span tree was
printed. Reading is a hypothesis; running is evidence.

## Reporting

Most severe first. For each: the file and line, one sentence on the defect, and the concrete
failure. Separate **confirmed** (you reproduced or traced it) from **plausible** (it looks
wrong and you could not confirm). Never pad the list — a review of six real findings is worth
more than twenty with four real ones, because the reader stops trusting the set.

If the change is sound, say so plainly and name what you checked, so the reader knows the
review had teeth.
