"""Live slice: an agent emits a full trace, exported to a hosted backend and read back (Phase 07).

Two proofs, both live:

* **Declarative, keyless-of-any-backend** — ``agents/observability.yaml`` declares an ``observability:``
  block, and that alone makes a real turn traced. We build the agent from the YAML (the published,
  zero-code path), confirm it carries a real observer, and run a live turn. Needs only OPENAI_API_KEY.

* **Hosted read-back** — for each of Opik / LangFuse / LangSmith, we export the *same* tool-calling
  turn to the backend over real OTLP, then query that backend's own API and assert the nested tree
  (agent → node → model → tool) landed. Each backend test skips unless its keys are set, so the suite
  never fake-passes. LangSmith ships spans off-box, so its export sets ``allow_saas_exporter``.

Set keys in ``.env`` (see ``.env.example``); run with ``pytest tests/test_observability.py -q``.
"""

from __future__ import annotations

import base64
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import pytest
from agentship import build_agent
from agentship.observability import NoOpObserver, semconv
from agentship_observability.config import ObservabilityConfig
from agentship_observability.factory import (
    _reset_providers_for_tests,
    build_tracer_provider,
)
from agentship_observability.otel import OTelObserver
from conftest import requires_live_key
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = str(REPO_ROOT / "agents" / "observability.yaml")

#: The live turn every proof runs — arithmetic phrased so the model reaches for the calculator tool,
#: which is what gives the trace its tool span (and therefore real nesting to read back).
QUESTION = "What is 21 * 2? Use the calculator."

#: Per-backend skip guards: a hosted read-back needs that backend's credentials, or it skips cleanly.
_needs_opik = pytest.mark.skipif(
    not os.environ.get("OPIK_API_KEY"), reason="set OPIK_API_KEY to prove the Opik export"
)
_needs_langfuse = pytest.mark.skipif(
    not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")),
    reason="set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to prove the LangFuse export",
)
_needs_langsmith = pytest.mark.skipif(
    not os.environ.get("LANGSMITH_API_KEY"), reason="set LANGSMITH_API_KEY to prove the LangSmith export"
)


@dataclass
class RunOutcome:
    """One export's identity: the OTel trace id, the isolating project, and the span names seen."""

    trace_id: str
    project: str
    span_names: list[str] = field(default_factory=list)


class _CapturingProcessor(SpanProcessor):
    """Record each ended span's name and the root trace id, alongside the real backend exporter.

    ``Result`` carries no trace id, and the read-back needs one (LangFuse keys on it). Riding on the
    same provider, this captures span names for an in-process sanity check and the root ``agent``
    span's trace id to query the backend with.
    """

    def __init__(self) -> None:
        """Start with an empty capture buffer."""
        self.names: list[str] = []
        self.trace_id: str = ""

    def on_start(self, span, parent_context=None) -> None:
        """No-op: capture happens on end, when the span name and context are final."""

    def on_end(self, span: ReadableSpan) -> None:
        """Record the span's name and, for the root ``agent`` span, its 32-hex trace id."""
        self.names.append(span.name)
        if span.name == semconv.SPAN_AGENT and span.context is not None:
            self.trace_id = format(span.context.trace_id, "032x")

    def shutdown(self) -> None:
        """No resources to release."""

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """Nothing is buffered here, so a flush is always immediately complete."""
        return True


async def _export_turn(backend: str, project: str) -> RunOutcome:
    """Run the live tool-calling turn once, exporting real OTLP spans to ``backend``, then flush.

    Builds a provider that exports only to ``backend`` (SaaS gate opened, since these tests are an
    explicit operator-run egress), rides a :class:`_CapturingProcessor` alongside to learn the trace
    id, runs one live turn through the agent, and force-flushes so batched spans ship before the
    read-back queries the backend.
    """
    _reset_providers_for_tests()  # a fresh provider so no stale capture/project header lingers
    config = ObservabilityConfig(exporters=[backend], allow_saas_exporter=True, capture_content=True)
    provider = build_tracer_provider(config)
    capture = _CapturingProcessor()
    provider.add_span_processor(capture)
    observer = OTelObserver(provider, capture_content=True)

    agent = build_agent(AGENT, observer=observer)  # explicit observer wins over the YAML's block
    await agent.run(QUESTION, session_id=project)
    provider.force_flush()
    return RunOutcome(trace_id=capture.trace_id, project=project, span_names=capture.names)


def _has_full_tree(span_names: list[str]) -> tuple[bool, str]:
    """Check span names cover the required agent/node/model/tool shape; explain what's missing."""
    checks = {
        "agent": semconv.SPAN_AGENT in span_names,
        "model": span_names.count(semconv.SPAN_MODEL) >= 1,
        "tool": any(n.startswith(semconv.TOOL_PREFIX) for n in span_names),
        "node": any(n.startswith(semconv.NODE_PREFIX) for n in span_names),
    }
    missing = [label for label, ok in checks.items() if not ok]
    return not missing, ("full tree present" if not missing else f"missing spans: {', '.join(missing)}")


def _poll(fetch, *, attempts: int = 15, delay: float = 2.0):
    """Call ``fetch`` until it returns something truthy or attempts run out (ingestion is eventual)."""
    for _ in range(attempts):
        value = fetch()
        if value:
            return value
        time.sleep(delay)
    return None


def _read_back_opik(outcome: RunOutcome) -> tuple[bool, str]:
    """Query Opik's REST API for the run's project and confirm its span tree."""
    base = os.getenv("OPIK_OTEL_ENDPOINT", "").split("/v1/private/otel")[0]
    base = base or "https://www.comet.com/opik/api"
    headers = {"Authorization": os.getenv("OPIK_API_KEY", "")}
    if workspace := os.getenv("OPIK_WORKSPACE"):
        headers["Comet-Workspace"] = workspace

    def fetch():
        resp = httpx.get(
            f"{base}/v1/private/spans",
            headers=headers,
            params={"project_name": outcome.project, "size": 200},
            timeout=30,
        )
        resp.raise_for_status()
        return [span.get("name", "") for span in resp.json().get("content", [])]

    names = _poll(fetch)
    if names is None:
        return False, f"no spans found in Opik project {outcome.project!r} after polling"
    return _has_full_tree(names)


def _read_back_langfuse(outcome: RunOutcome) -> tuple[bool, str]:
    """Query LangFuse's public API by OTLP trace id and confirm its observation tree."""
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/")
    public, secret = os.getenv("LANGFUSE_PUBLIC_KEY", ""), os.getenv("LANGFUSE_SECRET_KEY", "")
    auth = "Basic " + base64.b64encode(f"{public}:{secret}".encode()).decode()

    def fetch():
        resp = httpx.get(
            f"{host}/api/public/observations",
            headers={"Authorization": auth},
            params={"traceId": outcome.trace_id, "limit": 100},
            timeout=30,
        )
        resp.raise_for_status()
        return [obs.get("name", "") for obs in resp.json().get("data", [])]

    names = _poll(fetch)
    if names is None:
        return False, f"no observations found in LangFuse for trace {outcome.trace_id!r}"
    return _has_full_tree(names)


def _read_back_langsmith(outcome: RunOutcome) -> tuple[bool, str]:
    """Query LangSmith via its SDK by project and map run types to the required tree shape."""
    from langsmith import Client

    client = Client()

    def fetch():
        return list(client.list_runs(project_name=outcome.project)) or None

    runs = _poll(fetch)
    if runs is None:
        return False, f"no runs found in LangSmith project {outcome.project!r}"
    run_types = {getattr(run, "run_type", "") for run in runs}
    missing = [t for t in ("chain", "llm", "tool") if t not in run_types]
    if missing:
        return False, f"missing run types: {', '.join(missing)} (saw {sorted(run_types)})"
    return True, f"{len(runs)} runs with types {sorted(run_types)}"


@requires_live_key
async def test_declarative_block_gives_a_live_traced_turn() -> None:
    """The YAML's ``observability`` block alone makes a live turn traced — the published zero-code path.

    Builds the agent straight from ``observability.yaml`` (no observer passed), so it exercises
    ``resolve_observer``: the block becomes a real observer, and a live tool-calling turn answers
    correctly while tracing runs.
    """
    agent = build_agent(AGENT)
    assert not isinstance(agent.observer, NoOpObserver)
    result = await agent.run(QUESTION)
    assert "42" in result.output


@requires_live_key
@_needs_opik
async def test_full_trace_exports_to_opik() -> None:
    """A live turn's full span tree is exported to Opik and read back with agent/node/model/tool."""
    project = f"agentship-obs-{uuid.uuid4().hex[:8]}"
    os.environ["OPIK_PROJECT_NAME"] = project
    outcome = await _export_turn("opik", project)
    exported_ok, exported_detail = _has_full_tree(outcome.span_names)
    assert exported_ok, f"export itself was incomplete: {exported_detail}"
    ok, detail = _read_back_opik(outcome)
    assert ok, detail


@requires_live_key
@_needs_langfuse
async def test_full_trace_exports_to_langfuse() -> None:
    """A live turn's full span tree is exported to LangFuse and read back by its OTLP trace id."""
    project = f"agentship-obs-{uuid.uuid4().hex[:8]}"
    outcome = await _export_turn("langfuse", project)
    exported_ok, exported_detail = _has_full_tree(outcome.span_names)
    assert exported_ok, f"export itself was incomplete: {exported_detail}"
    assert outcome.trace_id, "no trace id captured to query LangFuse with"
    ok, detail = _read_back_langfuse(outcome)
    assert ok, detail


@requires_live_key
@_needs_langsmith
async def test_full_trace_exports_to_langsmith() -> None:
    """A live turn's full span tree is exported to LangSmith and read back as chain/llm/tool runs."""
    project = f"agentship-obs-{uuid.uuid4().hex[:8]}"
    os.environ["LANGSMITH_PROJECT"] = project
    outcome = await _export_turn("langsmith", project)
    exported_ok, exported_detail = _has_full_tree(outcome.span_names)
    assert exported_ok, f"export itself was incomplete: {exported_detail}"
    ok, detail = _read_back_langsmith(outcome)
    assert ok, detail
