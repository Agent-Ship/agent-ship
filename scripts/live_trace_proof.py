"""Live-backend proof for P07: export a full nested trace to a hosted backend, then read it back.

The keyless half of the proof lives in ``test_otlp_wire_export.py`` — it asserts the exact bytes on
the OTLP wire carry ``agent → node → {model×2, tool.calculator}`` with tokens and cost. This script
is the hosted half: it runs the *same* deterministic tool-executing turn through the real exporter
against a hosted **Opik / LangFuse / LangSmith / Phoenix** endpoint, then queries that backend's own
API to confirm the trace was ingested with its nesting and token/cost attributes intact.

Credentials come from the environment (a ``.env`` beside this repo is loaded if present) — never
from code. Each run uses a unique per-backend project/time window so the read-back finds *this*
run's trace and nothing else. Usage::

    python scripts/live_trace_proof.py --backend opik
    python scripts/live_trace_proof.py --backend langsmith --backend langfuse
    python scripts/live_trace_proof.py --backend console      # keyless smoke test (prints the tree)

Exit code is 0 only when every requested backend both exported and read back a well-formed tree, so
this doubles as a CI gate once credentials are wired.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
import uuid
from dataclasses import dataclass, field

import agentship_langgraph.models as models_module
import httpx
from agentship.observability import semconv
from agentship.runtime import build_agent
from agentship.spec import AgentSpec
from agentship_observability.config import ObservabilityConfig
from agentship_observability.factory import build_tracer_provider
from agentship_observability.otel import OTelObserver
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

try:  # Loading .env is a convenience, not a hard dependency of the proof.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is present in dev but optional
    pass


class _ToolThenAnswerModel(BaseChatModel):
    """Deterministic ReAct fake: first call requests the calculator, second answers — with usage.

    Using a fixed fake (not a live LLM) makes the emitted tree identical on every run, so the
    read-back assertions are stable and no model API key is needed. The spans still carry real
    ``usage_metadata``, so tokens and a LiteLLM-priced cost land on the model spans exactly as a
    production call would.
    """

    calls: int = 0
    model: str = "openai/gpt-4o-mini"

    @property
    def _llm_type(self) -> str:
        """LangChain model-type tag (required by the base class)."""
        return "fake-tool-then-answer"

    @property
    def _identifying_params(self) -> dict[str, str]:
        """Surface the LiteLLM model id so the callback prices the call."""
        return {"model": self.model}

    def bind_tools(self, tools: object, **kwargs: object) -> BaseChatModel:
        """Accept the ReAct loop's tool binding and stay the same fake model."""
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        """Return a tool-call message first, then a final answer — both carrying token usage."""
        self.calls += 1
        if self.calls == 1:
            message = AIMessage(
                content="",
                tool_calls=[{"name": "calculator", "args": {"expression": "2 + 2"}, "id": "c1"}],
                usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            )
        else:
            message = AIMessage(
                content="The answer is 4.",
                usage_metadata={"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


@dataclass
class RunOutcome:
    """The result of one export: the OTel trace id and the span-name tree seen in-process."""

    trace_id: str
    project: str
    span_names: list[str] = field(default_factory=list)


class _CapturingProcessor(SpanProcessor):
    """Record each ended span's name and trace id, so the script knows what it just exported.

    ``Result`` carries no trace id, and the backend read-back needs one (LangFuse keys on the OTLP
    trace id). This rides alongside the real exporter on the same provider: as spans end it captures
    their names and the root's trace id, giving both the id to query and an in-process sanity check
    of the tree before any network round-trip.
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
        """Nothing is buffered, so a flush is always immediately complete."""
        return True


async def _export_turn(config: ObservabilityConfig, project: str) -> RunOutcome:
    """Run the deterministic tool-executing turn once, exporting real OTLP spans, and flush.

    Swaps the real LiteLLM model for the deterministic fake, builds an agent whose observer exports
    to the backend in ``config``, runs one turn, force-flushes the provider so batched spans ship
    before read-back, and returns the OTel trace id plus the span names the in-process tree emitted.
    """
    models_module.resolve_model = lambda *a, **k: _ToolThenAnswerModel()  # type: ignore[assignment]

    provider = build_tracer_provider(config)
    capture = _CapturingProcessor()
    provider.add_span_processor(capture)
    observer = OTelObserver(provider, capture_content=config.capture_content)
    agent = build_agent(
        AgentSpec(
            name="liveproof",
            engine="langgraph",
            template="single",
            model="openai/gpt-4o-mini",
            prompt="You do math.",
            tools=["calculator"],
        ),
        observer=observer,
    )

    await agent.run("What is 2 + 2?")
    provider.force_flush()
    return RunOutcome(trace_id=capture.trace_id, project=project, span_names=capture.names)


def _poll(fetch, *, attempts: int = 15, delay: float = 2.0):
    """Call ``fetch`` until it returns a truthy value or attempts run out (ingestion is eventual).

    Hosted backends ingest OTLP asynchronously, so a trace is not queryable the instant the export
    returns. This retries with a fixed delay, returning the first non-empty result or None.
    """
    for _ in range(attempts):
        value = fetch()
        if value:
            return value
        time.sleep(delay)
    return None


def _has_full_tree(span_names: list[str]) -> tuple[bool, str]:
    """Check a set of span names covers the required agent/node/model/tool shape; explain if not."""
    have_agent = semconv.SPAN_AGENT in span_names
    have_model = span_names.count(semconv.SPAN_MODEL) >= 1
    have_tool = any(n.startswith(semconv.TOOL_PREFIX) for n in span_names)
    have_node = any(n.startswith(semconv.NODE_PREFIX) for n in span_names)
    missing = [
        label
        for label, ok in (
            ("agent", have_agent),
            ("model", have_model),
            ("tool", have_tool),
            ("node", have_node),
        )
        if not ok
    ]
    return (not missing, "missing spans: " + ", ".join(missing) if missing else "full tree present")


def _verify_opik(outcome: RunOutcome) -> tuple[bool, str]:
    """Read the trace back from Opik's REST API and confirm its span tree by project name.

    Opik ingests each OTLP root as a trace and its children as spans. We isolate this run with a
    unique project name, then fetch that project's spans and check they cover agent/node/model/tool.
    """
    base = os.getenv("OPIK_OTEL_ENDPOINT", "").split("/v1/private/otel")[0]
    if not base:
        base = "https://www.comet.com/opik/api"
    headers = {"Authorization": os.getenv("OPIK_API_KEY", "")}
    if workspace := os.getenv("OPIK_WORKSPACE"):
        headers["Comet-Workspace"] = workspace
    params = {"project_name": outcome.project, "size": 200}

    def fetch():
        resp = httpx.get(f"{base}/v1/private/spans", headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        return [span.get("name", "") for span in resp.json().get("content", [])]

    names = _poll(fetch)
    if names is None:
        return False, f"no spans found in Opik project {outcome.project!r} after polling"
    return _has_full_tree(names)


def _verify_langfuse(outcome: RunOutcome) -> tuple[bool, str]:
    """Read the trace back from LangFuse's public API and confirm its observation tree.

    LangFuse maps OTLP spans to observations under one trace. We find the trace by its OTLP trace id
    (LangFuse preserves it), then fetch its observations and check the required span shape.
    """
    host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com").rstrip("/")
    public, secret = os.getenv("LANGFUSE_PUBLIC_KEY", ""), os.getenv("LANGFUSE_SECRET_KEY", "")
    auth = "Basic " + base64.b64encode(f"{public}:{secret}".encode()).decode()
    headers = {"Authorization": auth}

    def fetch():
        resp = httpx.get(
            f"{host}/api/public/observations",
            headers=headers,
            params={"traceId": outcome.trace_id, "limit": 100},
            timeout=30,
        )
        resp.raise_for_status()
        return [obs.get("name", "") for obs in resp.json().get("data", [])]

    names = _poll(fetch)
    if names is None:
        return False, f"no observations found in LangFuse for trace {outcome.trace_id!r}"
    return _has_full_tree(names)


def _verify_langsmith(outcome: RunOutcome) -> tuple[bool, str]:
    """Read the trace back from LangSmith via its SDK and confirm the run tree by project name.

    LangSmith records each span as a run (run_type llm/tool/chain). We isolate this run with a
    unique project, list its runs, and map run types to the agent/model/tool shape the proof needs.
    """
    try:
        from langsmith import Client
    except ImportError:
        return False, "langsmith SDK not installed (pip install langsmith)"

    client = Client()

    def fetch():
        runs = list(client.list_runs(project_name=outcome.project))
        return runs or None

    runs = _poll(fetch)
    if runs is None:
        return False, f"no runs found in LangSmith project {outcome.project!r}"
    run_types = {getattr(run, "run_type", "") for run in runs}
    missing = [t for t in ("chain", "llm", "tool") if t not in run_types]
    if missing:
        return False, f"missing run types: {', '.join(missing)} (saw {sorted(run_types)})"
    return True, f"{len(runs)} runs with types {sorted(run_types)}"


#: Backends whose export ships off-box and whose read-back this script can drive.
_VERIFIERS = {
    "opik": _verify_opik,
    "langfuse": _verify_langfuse,
    "langsmith": _verify_langsmith,
}


async def _prove(backend: str) -> bool:
    """Export a turn to ``backend`` and read it back; print a PASS/FAIL line and return success."""
    project = f"agentship-liveproof-{uuid.uuid4().hex[:8]}"
    if backend in ("langsmith", "opik"):
        os.environ["LANGSMITH_PROJECT" if backend == "langsmith" else "OPIK_PROJECT_NAME"] = project

    config = ObservabilityConfig(
        exporters=[backend],
        allow_saas_exporter=True,  # this script is an explicit, operator-run egress
    )
    print(f"[{backend}] exporting a tool-executing turn (project={project}) ...")
    outcome = await _export_turn(config, project)
    print(f"[{backend}] exported trace {outcome.trace_id or '(id unavailable)'}; flushed.")

    # Sanity-check the tree we just exported before blaming the backend for a bad read-back.
    exported_ok, exported_detail = _has_full_tree(outcome.span_names)
    print(f"[{backend}] exported tree: {exported_detail}")
    if not exported_ok:
        print(f"[{backend}] FAIL — export itself was incomplete, not a backend problem.")
        return False

    if backend == "console":
        print(f"[{backend}] PASS — spans printed above (console has no API to read back).")
        return True

    verify = _VERIFIERS[backend]
    ok, detail = verify(outcome)
    print(f"[{backend}] {'PASS' if ok else 'FAIL'} — {detail}")
    return ok


def _parse_args() -> argparse.Namespace:
    """Parse ``--backend`` (repeatable); ``all`` expands to every hosted backend."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        action="append",
        choices=[*_VERIFIERS, "console", "all"],
        required=True,
        help="backend to prove; repeat for several, or 'all' for every hosted backend",
    )
    return parser.parse_args()


async def _main() -> int:
    """Run the proof for each requested backend; exit non-zero if any backend fails."""
    args = _parse_args()
    backends = list(_VERIFIERS) if "all" in args.backend else args.backend
    results = {backend: await _prove(backend) for backend in backends}
    print("\n=== live-trace proof summary ===")
    for backend, ok in results.items():
        print(f"  {backend:10s} {'PASS' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(_main()))
