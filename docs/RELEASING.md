# Releasing

AgentShip ships as **six distributions released together**: `agentship-sdk` (the meta-package)
plus `agentship-core`, `-langgraph`, `-service`, `-observability` and `-cli`.

Pushing a tag matching `v*` is the only thing that publishes. Pushing to a branch runs the
test gate and reaches no index, so `main` is never one accidental push away from a release.

## Versioning

**One version across all six**, and every sibling dependency pinned to exactly it.

They are only ever tested together, so a mixed set is a combination nobody has run. Before
this was enforced, the meta-package depended on its siblings with no constraint at all —
`pip install agentship-sdk==0.2.0` could legally resolve `agentship-core==0.9.0`.

```bash
python scripts/versions.py --check       # CI gate; non-zero on any drift
python scripts/versions.py --set 0.0.2   # bump all six and every pin at once
```

Never hand-edit a version. Six hand-edited strings is how a release ships with one missed.

`--check` verifies the six agree **with each other**, not that you bumped anything. Ordinary
commits never touch a version, so it passes indefinitely while work continues.

### Where we are: `0.0.x`

**We are on `0.0.x` and staying there for now.** The phases that define v0.1 are still in
flight, and `0.1.0` is a claim — "usable, still moving" — the tree cannot make yet. `0.0.x`
says what is true: the packages exist, they are being tested in the open, and nothing is
promised. The next release is `0.0.2`.

### What 0.x will mean

While the version starts with `0.`, **the API can break between minor versions**. `0.2.0`
may break code written against `0.1.0`. Pin exactly (`agentship-sdk==0.1.0`) if that matters
to you. From `1.0.0` we follow semantic versioning and breaking changes wait for a major.

This is stated because "0.x means unstable" is a convention, not a rule a resolver knows.

## Branching

**Trunk-based. Releases are cut from `main`; there are no release branches.**

```
feature branch  ──►  PR  ──►  main  ──►  tag  ──►  published
```

`main` stays releasable at all times, so whatever is on it when you tag is what ships. That
is the real control point: a fix reaches users because it was merged *and* a tag was cut
after it — there is no per-fix switch. Merge to `main` only what you would be content to
publish in the next release.

A tag points at a commit permanently, so a released tree can always be reconstructed:

```bash
git checkout v0.0.2   # exactly what produced those wheels
```

**When we would add a release branch — and why we have not.** LangGraph keeps long-lived `0.4`
and `0.6` branches to patch older lines while `main` moves on. That only matters once you
support more than one line at a time. With a single supported line, a hotfix is:

```bash
git checkout -b hotfix/0.0.3 v0.0.2   # branch from the released TAG, not main
git cherry-pick <the fix>             # take only that commit
python scripts/versions.py --set 0.0.3
git tag v0.0.3 && git push origin v0.0.3
```

Reach for that only when `main` has moved somewhere you cannot ship yet. Normally, tag `main`.

## Cadence — when to cut a release

A published version can never change, so any fix that reaches users needs a new number. That
does **not** mean a release per fix. Batch them:

- **Routine fixes and features** — let them accumulate on `main`, ship with the next release.
- **The published build is unusable** — release immediately, even for one commit.

`0.0.1` was the second kind: it could not run an agent at all. Most fixes are the first kind.
Releasing on every commit burns numbers and produces an event — six uploads, a tag, a GitHub
Release, a changelog entry — for changes nobody was waiting on.

## Cutting a release

```bash
python scripts/versions.py --set 0.0.2
python scripts/versions.py --check
make test

git commit -am "release: 0.0.2"
git tag v0.0.2
git push origin main --tags
```

The version commit does nothing else — six `version =` lines and the pins between them — so
the tag marks an unambiguous point in history.

Pushing the tag runs `.github/workflows/release.yml`:

| Job | What it does | Gate |
|---|---|---|
| `build` | lockstep + tag/version check, build all six, `twine check` | a tag disagreeing with the packages stops here |
| `testpypi` | publish to TestPyPI | every tag |
| `verify-testpypi` | **install back out of the index** and smoke-test | a pin no resolver can satisfy stops here |
| `pypi` | publish to PyPI | only after the above passes |
| `github-release` | tag notes, distributions attached | |

`workflow_dispatch` runs the same pipeline but stops at TestPyPI, so the whole thing can
be rehearsed without cutting a tag.

### Why install back out of TestPyPI

Every other check installs local wheels by file path. That cannot catch a sibling pin no
index can satisfy, or a dependency that was never declared — the failures that only appear
once a resolver has to find the packages itself. It is the step that turns "the build
worked" into "a user can install this".

## Publishing credentials

Publishing supports both, and the workflow picks by input:

| `auth` input | Credential | When |
|---|---|---|
| `token` (default, and what a tag uses) | `PYPI_API_TOKEN` / `TEST_PYPI_API_TOKEN` repo secrets | today |
| `trusted-publishing` | OIDC — no stored secret | once configured below |

Trusted Publishing is where this should land: GitHub proves the workflow's identity and PyPI
issues a short-lived credential, so no long-lived token sits in the repository. Until every
project is configured for it, releases go out on stored tokens.

Configure once per project at <https://pypi.org/manage/account/publishing/>. All six use
the same owner/repo/workflow and differ only by **environment**:

| PyPI project | Environment (PyPI) | Environment (TestPyPI) |
|---|---|---|
| `agentship-core` | `pypi-agentship-core` | `testpypi-agentship-core` |
| `agentship-langgraph` | `pypi-agentship-langgraph` | `testpypi-agentship-langgraph` |
| `agentship-service` | `pypi-agentship-service` | `testpypi-agentship-service` |
| `agentship-observability` | `pypi-agentship-observability` | `testpypi-agentship-observability` |
| `agentship-cli` | `pypi-agentship-cli` | `testpypi-agentship-cli` |
| `agentship-sdk` | `pypi-agentship-sdk` | `testpypi-agentship-sdk` |

Owner `Agent-Ship`, repository `agent-ship`, workflow `release.yml` for every row.

**Why an environment per package.** PyPI allows only ONE pending publisher per
`(owner, repository, workflow, environment)` combination. With a single shared
environment, the first project registers and the second fails with *"a pending trusted
publisher matching this configuration has already been registered for a different project
name"*. That is also why the workflow publishes each package in its own matrix job: a job
may only publish the project its OIDC token is scoped to.

Until that exists the publish steps fail — deliberately, rather than falling back to a
stored secret.

## Claiming the six names the first time

PyPI allows at most **three pending trusted publishers at once**, and a pending publisher
stops counting once it is used — publishing converts it into a normal publisher on the
project it just created. So the first claim goes in two waves:

```
1. Register pending publishers for any THREE of the six
2. Actions -> Release -> Run workflow    packages: <those three, comma-separated>
3. Register pending publishers for the remaining three
4. Actions -> Release -> Run workflow    packages: <those three>
```

**The only ordering rule: `agentship-sdk` goes in the second wave.** It pins every sibling,
so publishing it first would put a distribution on the index whose dependencies cannot be
resolved. Which of the other five go in which wave does not matter — publishing only
uploads a file; nothing is resolved until something installs it.

A partial run skips the install-back-out check, since resolving `agentship-sdk` needs all
six present. Once every project exists, leave `packages` on `all` and it never comes up
again.

## State of the six names

- [x] **Reserved on PyPI** (2026-09-06) — all six exist at `0.0.1`
- [ ] Trusted Publishing configured for all six, on both indexes — publishing currently
      uses stored tokens (see above); the switch is `auth: trusted-publishing`
- [ ] Yank `0.0.1` — it is live and cannot run an agent. Yanking hides it from every
      resolver (`pip install agentship-sdk` skips it) without freeing the number, which is
      burned regardless. Done per-project on pypi.org → Manage → Releases → Yank.

**`0.0.1` is permanent and broken.** It shipped before the observability default was fixed,
so `agentship run` failed on the README's own quickstart. It cannot be replaced — PyPI
versions are immutable — only superseded by `0.0.2` and yanked.

## When 0.1.0 happens

Not yet, and not by accident. `0.1.0` is the first version that claims to be *usable*, so it
waits until the v0.1 phase tail is closed — see `.spec-dev/STATUS.md` for what remains. Until
then every release is `0.0.x`, whatever it contains.
