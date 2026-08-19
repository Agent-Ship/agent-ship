# agentship-core

The AgentShip kernel: spec, context, runtime, registry, errors, middleware, and the zero-dependency `echo` engine. Vendor-free — depends only on `pydantic` and `pyyaml`. Import as `agentship`.

## Vendor-free by contract — and guarded against drift

The kernel cannot import OpenTelemetry, `a2a-sdk`, or any other vendor library (design §4.6): engine
and eval hooks must work with only `agentship-core` installed. That forces us to keep a few small
implementations of our own — the semantic-convention key constants, the in-memory
`RecordingObserver`, the A2A wire models, and the JWT/JWKS seam. We do not just trust those to match
the standards they claim to speak; each is pinned to its upstream by a **conformance guard** — a test
in the relevant package that validates our output against the real library. So the code stays thin and
vendor-free, and it still can't silently drift.

See [`docs/decisions/0001-integrate-not-invent.md`](../../docs/decisions/0001-integrate-not-invent.md)
for the delegations and the keep-thin-and-guard decisions.
