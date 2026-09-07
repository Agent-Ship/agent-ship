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
python scripts/versions.py --set 0.1.0   # bump all six and every pin at once
```

Never hand-edit a version. Six hand-edited strings is how a release ships with one missed.

### What 0.x means

While the version starts with `0.`, **the API can break between minor versions**. `0.2.0`
may break code written against `0.1.0`. Pin exactly (`agentship==0.1.0`) if that matters
to you. From `1.0.0` we follow semantic versioning and breaking changes wait for a major.

This is stated because "0.x means unstable" is a convention, not a rule a resolver knows.

## Cutting a release

```bash
python scripts/versions.py --set 0.1.0
python scripts/versions.py --check
pytest -q

git commit -am "release: 0.1.0"
git tag v0.1.0
git push origin main --tags
```

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

**There is no API token in this repository.** Publishing uses PyPI Trusted Publishing
(OIDC): GitHub proves the workflow's identity and PyPI issues a short-lived credential.

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

## Before the first public release

- [ ] Configure Trusted Publishing for all six projects, on both indexes
- [ ] Reserve the six names on PyPI (a name taken later is a rename)
- [ ] Rehearse with `workflow_dispatch` and install from TestPyPI by hand
- [ ] Decide the first public version — `0.1.0` says "usable, still moving"
