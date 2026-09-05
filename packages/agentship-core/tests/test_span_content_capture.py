"""A trace UI must be able to show what went in and what came out, per span.

Content was captured under ``gen_ai.input.messages`` / ``gen_ai.output.messages`` on model
spans only. Two consequences a user hits immediately in Opik:

* the keys those UIs actually render for content are the OpenInference pair
  ``input.value`` / ``output.value`` — we already mirror token counts into
  ``llm.token_count.*`` for exactly this reason, but never mirrored the content — so the
  panels came up empty;
* the ROOT agent span carried nothing, so the one thing a reader wants first — what the user
  asked and what the agent finally answered — was absent from the trace's top row.

All of it stays behind ``capture_content``, which is off by default: this is the PHI gate.
"""

from __future__ import annotations

from agentship.observability import semconv
from agentship.observability.recorder import RecordingObserver
from agentship.runtime import build_agent
from agentship.spec import AgentSpec, ObservabilitySpec


class _CapturingRecorder(RecordingObserver):
    """A recorder that opts into content, standing in for a configured OTel observer."""

    capture_content = True


async def _root(capture: bool):
    """Run one echo turn and return the root span, with content capture on or off."""
    agent = build_agent(
        AgentSpec(
            name="a",
            engine="echo",
            observability=ObservabilitySpec(capture_content=capture, exporters=[]),
        )
    )
    observer = _CapturingRecorder() if capture else RecordingObserver()
    agent.observer = observer
    await agent.run("what is 6 times 7?")
    return observer.roots[0]


async def test_the_root_span_carries_what_was_asked_and_answered():
    """The trace's top row shows the turn's input and output, not just its identity."""
    root = await _root(capture=True)
    assert "6 times 7" in str(root.attrs.get(semconv.OI_INPUT_VALUE, "")), (
        f"the user's question is missing from the root span: {sorted(root.attrs)}"
    )
    assert root.attrs.get(semconv.OI_OUTPUT_VALUE), "the answer is missing from the root span"


async def test_content_is_off_unless_asked_for():
    """The PHI gate holds: no content anywhere without capture_content."""
    root = await _root(capture=False)
    assert semconv.OI_INPUT_VALUE not in root.attrs
    assert semconv.OI_OUTPUT_VALUE not in root.attrs


def test_the_openinference_content_keys_are_the_upstream_ones():
    """Adopted, not invented — these are the keys Opik and Phoenix render content from."""
    assert semconv.OI_INPUT_VALUE == "input.value"
    assert semconv.OI_OUTPUT_VALUE == "output.value"
