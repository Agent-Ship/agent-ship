# Releasing

> **CI and the release pipeline are currently PAUSED.** Every workflow runs
> manual-only (Actions → the workflow → Run workflow). To resume, uncomment the `on:`
> block at the top of the workflow file and delete the PAUSED banner above it.

AgentShip ships as **six distributions released together**: `agentship` (the meta-package)
plus `agentship-core`, `-langgraph`, `-service`, `-observability` and `-cli`.

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

Configure once per project at <https://pypi.org/manage/account/publishing/>:

- owner `Agent-Ship`, repository `agent-ship`, workflow `release.yml`
- environment `pypi` (and the same on TestPyPI with environment `testpypi`)

Until that exists the publish steps fail — deliberately, rather than falling back to a
stored secret.

## Before the first public release

- [ ] Configure Trusted Publishing for all six projects, on both indexes
- [ ] Reserve the six names on PyPI (a name taken later is a rename)
- [ ] Rehearse with `workflow_dispatch` and install from TestPyPI by hand
- [ ] Decide the first public version — `0.1.0` says "usable, still moving"
