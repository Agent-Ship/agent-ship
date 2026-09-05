"""The engine behind ``agentship verify`` — AgentShip's "verifiable agents" proof.

``verify`` is the one themed report that proves two honesty claims at once:

1. **No over-claims.** Every installed engine genuinely honours the capabilities it
   declares (and fails fast when asked for one it does not) — the engine×capability
   conformance grid from :mod:`agentship.conformance`.
2. **The wired contracts hold.** A project's agents load, validate against their
   engines, and the framework's cross-cutting promises (observability span tree,
   service auth/tenant isolation, A2A card honesty) still stand.

Each check is one :class:`Section`. The cardinal rule is *declare, don't fake*
(DESIGN §11): a section whose optional dependency is not installed, or which has no
target to check, reports **SKIPPED with a reason** — never a fake green. Only a real
failure (an over-claim, an invalid spec, a broken contract) fails the run.

This module owns no Click surface; :func:`run_verification` returns a
:class:`VerifyReport` that the ``verify`` command in :mod:`agentship_cli.main`
renders and turns into an exit code.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path

from agentship.conformance import CellResult, run_capability_grid


@dataclass(frozen=True)
class Section:
    """One line of the verify report: a named check and how it fared.

    ``name`` is the human label shown in the report. ``passed``/``total`` count the
    sub-checks that held out of those attempted (``0/0`` for a skipped section).
    ``skipped_reason`` is ``None`` when the section actually ran; when set, the
    section did not run (a missing optional dependency or no target to check) and the
    reason is shown verbatim — a skip never fails the overall run. ``details`` carries
    extra lines to surface under the section (e.g. each named over-claim).
    """

    name: str
    passed: int
    total: int
    skipped_reason: str | None
    details: tuple[str, ...] = ()

    @property
    def skipped(self) -> bool:
        """Whether this section was skipped (declared, not faked) rather than run."""
        return self.skipped_reason is not None

    @property
    def failed(self) -> bool:
        """Whether this section ran and had a real failure (some sub-check did not hold).

        A skipped section is never failed — that is the honesty rule: a missing code
        path or dependency is reported as SKIPPED, never counted against the run.
        """
        return not self.skipped and self.passed < self.total


@dataclass(frozen=True)
class VerifyReport:
    """The whole ``agentship verify`` result: every section plus the over-claim roll-up.

    ``over_claims`` names each conformance ``prove`` cell that failed — an engine that
    declared a capability it does not actually honour, the headline dishonesty
    ``verify`` exists to catch. ``ok`` is true when no section had a *real* failure
    (skips do not count), which the CLI turns into the process exit code.
    """

    sections: tuple[Section, ...]
    over_claims: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True when no section really failed — skipped sections never fail the run."""
        return not any(section.failed for section in self.sections)


def _langgraph_offline() -> Callable[[str], AbstractContextManager] | None:
    """Return the langgraph offline model provider, or ``None`` if the extra is absent.

    The grid's positive cells for a model-backed engine must run without a real
    provider key; :func:`agentship_langgraph.testing.offline` supplies the fake-model
    seam. It lives in an engine extra, so we import it lazily and degrade to ``None``
    (grid runs unpatched — fine for the model-free ``echo`` engine) when it is not
    installed, rather than making langgraph a hard dependency of ``verify``.
    """
    try:
        from agentship_langgraph.testing import offline
    except ImportError:
        return None
    return offline


async def _section_capability_grid() -> tuple[Section, tuple[str, ...]]:
    """Run the engine×capability conformance grid and summarise it as a section.

    Every cell (positive ``prove`` and negative ``reject``) counts toward the section
    total; a failed ``prove`` cell is additionally recorded as a named over-claim
    (``engine.capability``) — the exact dishonesty ``verify`` is built to surface. The
    grid never raises: each failure is a failing :class:`CellResult`, so this always
    returns a complete section.
    """
    results: list[CellResult] = await run_capability_grid(offline=_langgraph_offline())
    passed = sum(1 for cell in results if cell.passed)
    over_claims = tuple(
        f"{cell.engine}.{cell.capability}"
        for cell in results
        if cell.kind == "prove" and not cell.passed
    )
    details = tuple(f"over-claim: {name} declared but not honoured" for name in over_claims)
    section = Section("engine×capability grid", passed, len(results), None, details)
    return section, over_claims


def _section_spec_validation(agents_dir: Path | None) -> Section:
    """Validate every spec in ``agents_dir`` with the same checks ``doctor`` runs.

    Skipped (honestly, with a reason) when no ``--agents-dir`` was given — there is
    nothing to validate. Otherwise each ``*.yaml`` is loaded and capability-gated via
    the shared :func:`agentship_cli.main._check_agent` helper (no duplication of
    doctor's logic): a spec passes iff that returns ``None``. Every invalid spec is
    listed with its reason and the section fails.
    """
    if agents_dir is None:
        return Section("spec validation", 0, 0, "no --agents-dir given (nothing to validate)")

    # Imported here (not at module load) to avoid a circular import with main.py.
    from agentship.errors import AgentShipError

    from .main import _agent_files, _check_agent

    files = _agent_files(agents_dir)
    if not files:
        return Section("spec validation", 0, 0, f"no *.yaml specs in {agents_dir}")

    passed = 0
    details: list[str] = []
    for path in files:
        try:
            reason = _check_agent(path)
        except AgentShipError as exc:
            reason = str(exc)
        if reason is None:
            passed += 1
        else:
            details.append(f"invalid spec {path.name}: {reason}")
    return Section("spec validation", passed, len(files), None, tuple(details))


async def _section_observability() -> Section:
    """Assert one real agent run emits the frozen root ``agent`` span (CONF-OBS-1).

    Skipped (with a reason) when the ``agentship-observability`` extra is not
    installed. Otherwise it builds an ``echo`` agent — no model, no key — attaches a
    :class:`RecordingObserver`, runs one turn, and asserts the recorded tree is
    exactly one root whose name/kind are the frozen ``agent`` span from SEMCONV. The
    model-free echo engine emits no child spans, so the contract checked here is the
    root-span shape the span tree hangs off; richer child structure is proven in the
    per-engine observability suite.
    """
    try:
        from agentship.observability import RecordingObserver, SpanKind, semconv
    except ImportError:
        return Section(
            "observability span-tree",
            0,
            0,
            "agentship-observability not installed — pip install 'agentship-sdk[observability]'",
        )

    from agentship.runtime import build_agent
    from agentship.spec import AgentSpec

    checks: list[tuple[str, bool]] = []
    obs = RecordingObserver()
    agent = build_agent(AgentSpec(name="verify-obs", engine="echo"), observer=obs)
    await agent.run("ping", user_id="verify")

    checks.append(("exactly one root span", len(obs.roots) == 1))
    if obs.roots:
        root = obs.roots[0]
        # The root span is named "agent <name>" so a backend's trace list identifies the agent;
        # the check is the prefix, since the suffix is the agent's own name.
        checks.append(
            ("root span is named 'agent <name>'", root.name.startswith(semconv.SPAN_AGENT))
        )
        checks.append(("root span kind is AGENT", root.kind is SpanKind.AGENT))
        # The trace view read-port surfaces the recorded tree the eval/audit phases read.
        view = obs.trace_view()
        span_names = [s.name for s in view.spans()]
        checks.append(("root visible via trace_view()", root.name in span_names))

    passed = sum(1 for _, ok in checks if ok)
    details = tuple(f"failed: {label}" for label, ok in checks if not ok)
    return Section("observability span-tree", passed, len(checks), None, details)


def _section_service_contracts(agents_dir: Path | None) -> Section:
    """Assert the service enforces auth + tenant isolation (mirrors P04 conformance).

    Skipped (with a reason) when the ``agentship-service`` extra is not installed or no
    ``--agents-dir`` was given (the section is about serving a project's agents). When
    it runs it builds the real app via ``create_app`` over an in-memory ``echo`` agent
    and a two-tenant API-key table, then drives a FastAPI ``TestClient`` to assert the
    same promises as ``test_phase04_service_security.py``:

    - an unauthenticated invoke is 401 (no open ingress);
    - a request without the agent's invoke scope is 403;
    - tenant B cannot read (404) or cancel (403) a task tenant A created, while A can;
    - the ``/healthz`` liveness probe is the sole public path.
    """
    if agents_dir is None:
        return Section("service contracts", 0, 0, "no --agents-dir given (nothing to serve)")
    try:
        from agentship_service import AgentRegistry, create_app
        from fastapi.testclient import TestClient
    except ImportError:
        return Section(
            "service contracts",
            0,
            0,
            "agentship-service not installed — pip install 'agentship-sdk[service]'",
        )

    import json

    from agentship.auth import ApiKeyAuthProvider, EnvApiKeyStore
    from agentship.runtime import build_agent
    from agentship.spec import AgentSpec

    keys = json.dumps(
        [
            {"key": "acme", "user": "u1", "tenant": "acme", "scopes": ["*"]},
            {"key": "beta", "user": "u2", "tenant": "beta", "scopes": ["*"]},
            {"key": "narrow", "user": "u3", "tenant": "acme", "scopes": ["agent:other:invoke"]},
        ]
    )
    agents = AgentRegistry([build_agent(AgentSpec(name="support", engine="echo"))])
    auth = ApiKeyAuthProvider(EnvApiKeyStore(raw=keys))
    client = TestClient(create_app(auth=auth, agents=agents))

    checks: list[tuple[str, bool]] = []
    invoke = "/v1/agents/support:invoke"

    def _invoke(key: str | None) -> int:
        """POST an invoke with the given API key (or none) and return the status code."""
        headers = {"x-api-key": key} if key is not None else {}
        return client.post(invoke, headers=headers, json={"input": "hi"}).status_code

    # (a) No unauthenticated ingress: a missing or unknown key is rejected.
    checks.append(("unauthenticated invoke rejected (401)", _invoke(None) == 401))
    checks.append(("unknown key rejected (401)", _invoke("nope") == 401))
    # A valid key with the right scope authenticates; one missing the scope is 403.
    checks.append(("valid key + scope allowed (200)", _invoke("acme") == 200))
    checks.append(("missing scope denied (403)", _invoke("narrow") == 403))

    # (b) Tenant isolation: B cannot read/cancel A's task; A can.
    created = client.post(
        "/v1/tasks", headers={"x-api-key": "acme"}, json={"agent": "support", "input": "do it"}
    )
    checks.append(("task created by tenant A (202)", created.status_code == 202))
    if created.status_code == 202:
        task_id = created.json()["id"]
        checks.append(
            (
                "tenant B cannot read A's task (404)",
                client.get(f"/v1/tasks/{task_id}", headers={"x-api-key": "beta"}).status_code
                == 404,
            )
        )
        checks.append(
            (
                "tenant B cannot cancel A's task (403)",
                client.post(
                    f"/v1/tasks/{task_id}:cancel", headers={"x-api-key": "beta"}
                ).status_code
                == 403,
            )
        )
        checks.append(
            (
                "owner A reads its own task (200)",
                client.get(f"/v1/tasks/{task_id}", headers={"x-api-key": "acme"}).status_code
                == 200,
            )
        )

    # (c) The liveness probe is the only public path.
    checks.append(("/healthz is public (200)", client.get("/healthz").status_code == 200))

    passed = sum(1 for _, ok in checks if ok)
    details = tuple(f"failed: {label}" for label, ok in checks if not ok)
    return Section("service contracts", passed, len(checks), None, details)


def _section_a2a_interop(agents_dir: Path | None) -> Section:
    """Assert every A2A-exposed spec renders a schema-valid, honest Agent Card.

    Skipped (with a reason) when no ``--agents-dir`` was given or no loaded spec sets
    ``a2a.expose: true`` — the interop layer is optional (Layer 0 agents never reach
    the wire), so having nothing exposed is a legitimate skip, not a pass. For each
    exposed agent it builds the card from the spec + the engine's real capabilities
    (never hand-written) and asserts the required card fields are present, its
    ``streaming`` claim matches the engine, and every declared security scheme is
    advertised in ``securitySchemes`` — the "declare, don't fake" card honesty.
    """
    if agents_dir is None:
        return Section("A2A interop", 0, 0, "no --agents-dir given (no agents to expose)")

    from agentship.a2a.card import build_agent_card
    from agentship.engines.base import ENGINES
    from agentship.errors import AgentShipError
    from agentship.spec import load_spec

    from .main import _agent_files

    exposed: list = []
    for path in _agent_files(agents_dir):
        try:
            spec = load_spec(path)
        except AgentShipError:
            continue  # invalid specs are the spec-validation section's job, not this one
        a2a = getattr(spec, "a2a", None)
        if a2a is not None and getattr(a2a, "expose", False):
            exposed.append(spec)

    if not exposed:
        return Section("A2A interop", 0, 0, "no spec exposes a2a (interop layer optional)")

    checks: list[tuple[str, bool]] = []
    required = {"name", "url", "capabilities", "securitySchemes"}
    for spec in exposed:
        engine_cls = ENGINES.get(spec.engine)
        if engine_cls is None:
            checks.append((f"{spec.name}: engine {spec.engine!r} installed", False))
            continue
        caps = engine_cls.capabilities
        card = build_agent_card(
            spec,
            caps,
            base_url="https://verify.local",
            security=spec.a2a.security,
            oauth2=spec.a2a.oauth2,
        )
        body = card.model_dump(mode="json", by_alias=True)
        checks.append((f"{spec.name}: card has required fields", required <= set(body)))
        checks.append(
            (
                f"{spec.name}: card streaming matches engine",
                body["capabilities"].get("streaming") == caps.streaming,
            )
        )
        checks.append(
            (
                f"{spec.name}: advertises its security schemes",
                all(scheme in body["securitySchemes"] for scheme in spec.a2a.security),
            )
        )

    passed = sum(1 for _, ok in checks if ok)
    details = tuple(f"failed: {label}" for label, ok in checks if not ok)
    return Section("A2A interop", passed, len(checks), None, details)


async def run_verification(agents_dir: Path | None, *, live: bool) -> VerifyReport:
    """Run every verify section and assemble the themed :class:`VerifyReport`.

    ``agents_dir`` (from ``--agents-dir``) scopes the project-level sections (spec
    validation, service contracts, A2A interop); when ``None`` those sections skip
    honestly. ``live`` is threaded for future live-provider checks — today every
    section is fully offline (the grid uses the fake-model seam, all agents use the
    model-free ``echo`` engine), so a run needs no provider keys. Sections whose
    optional dependency is absent report SKIPPED rather than failing.
    """
    _ = live  # reserved: all current sections run offline; live checks land later.

    grid_section, over_claims = await _section_capability_grid()
    sections = (
        grid_section,
        _section_spec_validation(agents_dir),
        await _section_observability(),
        _section_service_contracts(agents_dir),
        _section_a2a_interop(agents_dir),
    )
    return VerifyReport(sections, over_claims)
