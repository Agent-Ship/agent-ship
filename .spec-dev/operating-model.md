# Operating model (team law)

Why this exists: the previous build shipped a 17-fix big-bang commit, ran no CI on its
branch, and marked things "done" that a test never proved. Never again. This is how we
work — a senior follows it verbatim.

## Definition of Done — a task is `[x]` only when ALL are true
```
CODE
 □ Scope = ONE task from a phase file. If it grew, split it.
 □ Docstring on every new/changed function & method.
 □ No bare except/catch; async ops handle errors; inputs validated at boundaries.
TEST (written FIRST)
 □ A test that FAILED before the code existed now PASSES (RED shown in the PR/commit).
 □ The offline test asserts the real mechanism — not a constant, not "fake returns X →
   assert X". Routing asserts input-dependent branches; streaming asserts len(chunks)>1.
 □ If the capability has a live path, its cassette is recorded and replays in CI.
GATE
 □ Full suite green locally AND CI green on the pushed branch.
DEMO
 □ examples/ exercises the new capability, with a test and refreshed recorded output.
TRACKER (the moment CI is green)
 □ phase file [~]→[x] with (commit SHA, proof test::name)
 □ tasks.md status reconciled
 □ Notion tracker row flipped, same SHA
```
Anti-gaming rule: an `[x]` line must NAME the passing assertion that proves it. If you
can't name one, it isn't done.

## The loop (per task)
1. **Claim** one task; flip it `[ ]→[~]` in the phase file + Notion. Branch off `main`
   (`feat/<phase>-<slug>`). Never commit features straight to `main`.
2. **Red** — write the failing test; capture the failure output.
3. **Green** — least code to pass; then the suite passes.
4. **Refactor** — clean only what you just wrote; suite stays green.
5. **Live proof** — record/refresh the cassette so CI replays it (no secrets in CI).
6. **Demo** — update `examples/` + its test + recorded output.
7. **Commit** — ONE task = ONE commit. Stage only this task's files. Conventional commit
   with `Phase:`/`Task:`/`Proof:` trailers. Soft cap ~8 files / 1 area per commit; a
   commit spanning many packages is the red flag we're eliminating. Commits are authored
   solely by the developer — **never** add `Co-Authored-By` or "Generated with …" trailers.
8. **Push & gate** — open a PR into `main`; CI must be green.
9. **Flip to done** — phase file → tasks.md → Notion, in that order (git is the source of
   truth; Notion mirrors), all referencing the same SHA.

## Live proofs, made real
Every real-model / network path is recorded once as a **VCR-style cassette** (committed,
secrets redacted) and replayed in CI on every PR — so "works end-to-end" is *verified*,
not asserted. A separate **nightly keyed job** re-runs them against real providers to
catch API drift; it never blocks a PR.

## CI & local gate (stood up in Phase 0)
- `main` and `feat/**` push + PRs run the full offline suite + replayed cassettes.
- Branch protection on `main`: PR required, CI required, no direct/force pushes.
- A pre-commit hook runs ruff + the changed area's tests + a soft files-per-commit cap.

## Tracking protocol (no drift)
Single writer per task flips all three surfaces in order (phase file → tasks.md → Notion),
each carrying the same commit SHA. A periodic audit greps every `[x]` for its
`(SHA, proof)` annotation and reverts any that don't resolve. This is the guard that
would have caught the "done-but-false" and stale-status failures of the last attempt.
