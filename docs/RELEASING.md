# Releasing

AgentShip ships as **a set of distributions released together**: `agentship-sdk` (the
meta-package) plus `agentship-core`, `-langgraph`, `-service`, `-observability`, `-cli` and
`-voice`. The authoritative list is `SIBLINGS` in `scripts/versions.py`.

Pushing a tag matching `v*` is the only thing that publishes. Pushing to a branch runs the
test gate and reaches no index, so `main` is never one accidental push away from a release.

## Versioning

**One version across all of them**, and every sibling dependency pinned to exactly it.

They are only ever tested together, so a mixed set is a combination nobody has run. Before
this was enforced, the meta-package depended on its siblings with no constraint at all —
`pip install agentship-sdk==0.2.0` could legally resolve `agentship-core==0.9.0`.

```bash
python scripts/versions.py --check       # CI gate; non-zero on any drift
python scripts/versions.py --set 0.0.3   # bump every package and every pin at once
```

Never hand-edit a version. A handful of hand-edited strings is how a release ships with one missed.

`--check` verifies the packages agree **with each other**, not that you bumped anything. Ordinary
commits never touch a version, so it passes indefinitely while work continues.

### Where we are

**The latest published release is `0.0.2`. The next release will be `0.0.3`.**

- The `pyproject.toml` files already say `0.0.3`. That release was prepared (its changelog
  section exists) but never tagged or uploaded, so the number is still free. When it is cut,
  `## [Unreleased]` is folded into the `[0.0.3]` section so the release notes cover both.
- The version is changed **only in the release commit**, right before tagging — never in a
  feature PR. Fixes collect under `## [Unreleased]` until then.

We stay on `0.0.x` while pre-release ([ADR 0005](decisions/0005-lockstep-versioning-and-tag-triggered-releases.md)):
the packages exist and are tested in the open, and no stability is promised. Releases continue
as `0.0.4`, `0.0.5`, … Moving to `0.1.0` — "usable, still moving" — is a separate decision.

### What 0.x will mean

While the version starts with `0.`, **the API can break between releases** — on `0.0.x`, any
release; from `0.1.0`, between minor versions. Pin exactly (`agentship-sdk==0.0.3`) if that
matters to you. From `1.0.0` we follow semantic versioning and breaking changes wait for a major.

This is stated because "0.x means unstable" is a convention, not a rule a resolver knows.

## Branching

**Trunk-based. Releases are cut from `main`; there are no release branches.**

```
feature branch  ──►  PR  ──►  main  ──►  tag  ──►  published
```

**`main` is protected** (repository ruleset "Protect main"). Every change — a release commit
included — arrives by pull request, with **one approval from someone other than the author** and
both CI checks (`Lint and test (offline)`, `Build wheels and test what ships`) green. Nobody can
push to it directly, force-push it or delete it. Admins can bypass only *within* a pull request,
for emergencies; a bypass is recorded on the PR. Tags are not branches, so pushing a tag is
unaffected.

`main` stays releasable at all times, so whatever is on it when you tag is what ships. That
is the real control point: a fix reaches users because it was merged *and* a tag was cut
after it — there is no per-fix switch. Merge to `main` only what you would be content to
publish in the next release.

A tag points at a commit permanently, so a released tree can always be reconstructed:

```bash
git checkout v0.0.3   # exactly what produced those wheels
```

**When we would add a release branch — and why we have not.** LangGraph keeps long-lived `0.4`
and `0.6` branches to patch older lines while `main` moves on. That only matters once you
support more than one line at a time. With a single supported line, a hotfix is:

```bash
git checkout -b hotfix/0.0.4 v0.0.3   # branch from the released TAG, not main
git cherry-pick <the fix>             # take only that commit
python scripts/versions.py --set 0.0.4
git tag v0.0.4 && git push origin v0.0.4
```

Reach for that only when `main` has moved somewhere you cannot ship yet. Normally, tag `main`.

## Cadence — when to cut a release

A published version can never change, so any fix that reaches users needs a new number. That
does **not** mean a release per fix. Batch them:

- **Routine fixes and features** — let them accumulate on `main`, ship with the next release.
- **The published build is unusable** — release immediately, even for one commit.

`0.0.1` was the second kind: it could not run an agent at all. Most fixes are the first kind.
Releasing on every commit burns numbers and produces an event — an upload per package, a tag, a GitHub
Release, a changelog entry — for changes nobody was waiting on.

## Release notes

**`docs/CHANGELOG.md` is the source; the GitHub release page is a copy of it.** Writing notes
twice means they disagree, and the copy people actually read — the release page — is the one
nobody remembers to update.

```bash
python scripts/changelog.py --check 0.0.3      # is there a section? (the release gate)
python scripts/changelog.py --section 0.0.3    # print exactly what the page will show
```

A section is a `## [<version>]` heading and everything up to the next `##`:

```markdown
## [0.0.2] — 2026-09-07

### Fixed
- ...
```

The `build` job runs `--check` **before anything is published**, so a tag with no notes fails
while it is still free to fix. `github-release` then publishes that section as the release body,
with GitHub's generated commit list appended below it for anyone who wants the detail.

Day to day: add entries under `## [Unreleased]` as you merge. Cutting a release is then just
renaming that heading to the version and dating it.

## Cutting a release

```bash
# 1. Notes first — the release gate checks for them, so write them before tagging.
#    Rename `## [Unreleased]` in docs/CHANGELOG.md to `## [0.0.3] — <date>`.
python scripts/changelog.py --check 0.0.3

# 2. Version across every package, plus every sibling pin.
python scripts/versions.py --set 0.0.3
python scripts/versions.py --check
make test

# 3. The release commit does nothing else — the changelog heading, the version lines and the
#    pins between them. It goes through a PR like any other change (main is protected).
git switch -c release/0.0.3
git commit -am "release: 0.0.3"
git push -u origin release/0.0.3
gh pr create --title "release: 0.0.3" --body "Version bump and changelog heading only."

# 4. Once it is approved and merged, tag the commit that landed on main — not the branch
#    commit, which a squash or rebase merge replaces.
git switch main && git pull
python scripts/versions.py --check     # must say 0.0.3
git tag v0.0.3
git push origin v0.0.3
```

The release commit does nothing else — the changelog heading, the `version =` lines and the
pins between them — so the tag marks an unambiguous point in history.

Pushing the tag runs `.github/workflows/release.yml`:

| Job | What it does | Gate |
|---|---|---|
| `build` | lockstep + tag/version check, build every package, `twine check` | a tag disagreeing with the packages stops here |
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

**There is no API token in this repository**, and none is needed. Publishing uses PyPI
Trusted Publishing (OIDC): GitHub proves the workflow's identity and PyPI issues a
short-lived credential. Verified — the repo has no secrets at repository, organisation or
environment scope, and `0.0.1` reached PyPI anyway.

The `auth` input exists to *test* the two paths, not to choose a strategy:

| `auth` input | `password` passed to the publish action | Result |
|---|---|---|
| `token` (default) | `secrets.PYPI_API_TOKEN` — unset, so empty | action falls back to OIDC |
| `trusted-publishing` | explicitly empty | OIDC |

Both end at OIDC while no secret exists. Setting `PYPI_API_TOKEN` is what would switch the
default to token auth; nothing does that today, and nothing should need to.

Configure once per project at <https://pypi.org/manage/account/publishing/>. All of them use
the same owner/repo/workflow and differ only by **environment**:

| PyPI project | Environment (PyPI) | Environment (TestPyPI) |
|---|---|---|
| `agentship-core` | `pypi-agentship-core` | `testpypi-agentship-core` |
| `agentship-langgraph` | `pypi-agentship-langgraph` | `testpypi-agentship-langgraph` |
| `agentship-service` | `pypi-agentship-service` | `testpypi-agentship-service` |
| `agentship-observability` | `pypi-agentship-observability` | `testpypi-agentship-observability` |
| `agentship-cli` | `pypi-agentship-cli` | `testpypi-agentship-cli` |
| `agentship-voice` | `pypi-agentship-voice` | `testpypi-agentship-voice` |
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

## Claiming names the first time

PyPI allows at most **three pending trusted publishers at once**, and a pending publisher
stops counting once it is used — publishing converts it into a normal publisher on the
project it just created. So the first claim goes in two waves:

```
1. Register pending publishers for any THREE of the six
2. Actions -> Release -> Run workflow    packages: <those three, comma-separated>
3. Register pending publishers for the remaining three
4. Actions -> Release -> Run workflow    packages: <those three>
```

That is how the original six were claimed. A package added later (`agentship-voice`) is a
single pending publisher per index, so it fits in one step: register it, then run the workflow
with `packages: agentship-voice` against that index.

**The only ordering rule: `agentship-sdk` goes in the second wave.** It pins every sibling,
so publishing it first would put a distribution on the index whose dependencies cannot be
resolved. Which of the other five go in which wave does not matter — publishing only
uploads a file; nothing is resolved until something installs it.

A partial run skips the install-back-out check, since resolving `agentship-sdk` needs every
sibling present. Once every project exists, leave `packages` on `all` and it never comes up
again.

## State of the names

Checked 2026-09-27 against both indexes.

| | PyPI | TestPyPI |
|---|---|---|
| The original six | ✅ `0.0.2` live; `0.0.1` yanked | ✅ `0.0.1`, `0.0.2` |
| `agentship-voice` | ❌ never published — name unclaimed | ❌ never published — name unclaimed |

`agentship-voice` joined the lockstep set in `0.0.3`, which was never released, so it has never
been uploaded anywhere. `release.yml` publishes it on every tag, and a tag cannot publish a
project whose trusted publisher does not exist.

### Remaining before `v0.0.3` can succeed

- [ ] **Register a pending publisher for `agentship-voice` on TestPyPI**
      (<https://test.pypi.org/manage/account/publishing/>), environment
      `testpypi-agentship-voice`, then Actions → Release → Run workflow with
      `packages: agentship-voice`, `target: testpypi`.
- [ ] **The same on PyPI** (<https://pypi.org/manage/account/publishing/>), environment
      `pypi-agentship-voice`, `target: pypi`. PyPI versions are immutable, so claim the name with
      the version you are about to release, or with a `0.0.3rc1`, never with a throwaway.
- [ ] **Rehearse** — Actions → Release → Run workflow, `target: testpypi`, `packages: all`.

TestPyPI versions are immutable too, so a rehearsal burns the number it publishes. Rehearse
with the version you intend to release and the tag will find it already there — harmless,
since `skip-existing: true` covers it, but the install-back-out check is then verifying the
rehearsal's upload rather than the tag's.

**`0.0.1` is permanent and broken.** It shipped before the observability default was fixed,
so `agentship run` failed on the README's own quickstart. It cannot be replaced — PyPI
versions are immutable — so it was superseded by `0.0.2` and yanked.
