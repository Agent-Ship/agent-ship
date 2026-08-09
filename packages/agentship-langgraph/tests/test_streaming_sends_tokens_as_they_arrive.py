"""Checks that streaming really sends tokens one at a time, not all at once.

Why this test needs a real key and runs on its own (not in the normal no-key
tests): the saved-recording test (``test_streaming_live.py``) shows that a real turn
comes out in many ``content`` pieces that join back into the answer. But a saved
recording plays every piece back instantly, so it can't show the pieces actually
*arrived one at a time over the network*. That means if streaming ever quietly broke
into "wait for the whole answer, then hand it out in pieces" (chopping one finished
message into many small ``content`` events), the recording test would still pass —
the pieces look the same, only the timing is gone.

This test covers that gap. It calls the **real** OpenAI API with a real key and
notes when each piece arrives. Real streaming makes the pieces arrive spread out in
time (a measured run showed ~45 pieces, first at 0.866s and last at 1.186s, about
0.32s apart, climbing steadily). If it broke into wait-then-chop, that spread would
collapse to nearly zero because every piece comes from an already-finished string in
the same instant. So we check the spread clears a small, safe floor.

Because it needs real timing it can only run with a real key, and it must never be
in the normal no-key tests — it skips itself without a key (via ``skipif``) and is
tagged ``live`` so the daily real-API job can pick it with ``-m live``.

    set -a; source .env; set +a       # load a real OPENAI_API_KEY
    python -m pytest -m live -q        # run the tests that call the real API
"""

from __future__ import annotations

import os
import time

# The real key state at pytest startup. Importing ``litellm`` (below, and in sibling
# test modules) loads any local ``.env`` via python-dotenv and injects
# ``OPENAI_API_KEY`` into ``os.environ`` — which would defeat the keyless ``skipif``
# on a dev box that has a ``.env`` on disk. The root ``conftest.py`` captures the
# pristine startup state before that happens; prefer it, and fall back to the live
# environment (correct in CI, where there is no ``.env`` to pollute it).
try:  # pragma: no cover - trivial import shim
    from conftest import HAS_OPENAI_KEY_AT_STARTUP as _HAS_OPENAI_KEY
except ImportError:  # pragma: no cover - fallback when conftest is not importable
    _HAS_OPENAI_KEY = bool(os.environ.get("OPENAI_API_KEY"))

import litellm  # noqa: E402
import pytest  # noqa: E402
from agentship.runtime import build_agent  # noqa: E402
from agentship.spec import AgentSpec  # noqa: E402

# LiteLLM's async path defaults to an aiohttp transport; force the plain httpx
# transport so the real streaming round-trip behaves like the recorded path. This
# also keeps the timing representative of the actual provider stream.
litellm.disable_aiohttp_transport = True

# Keep the cost map local so no extra HTTP fetch perturbs the timing measurement.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# A conservative floor for the arrival spread, in seconds. The measured real spread
# was ~0.32s; 0.05s is ~6x margin, so a genuine stream comfortably clears it and the
# test is not flaky — but a buffer-then-rechunk regression collapses the spread to
# ~0s (all chunks minted in the same instant) and trips this floor.
MIN_ARRIVAL_SPREAD_S = 0.05


@pytest.mark.live
@pytest.mark.skipif(
    not _HAS_OPENAI_KEY,
    reason="needs a real OPENAI_API_KEY; runs in the daily real-API job, not the no-key tests",
)
async def test_real_streaming_arrives_one_piece_at_a_time():
    """The pieces of a real answer arrive spread out in time, not all at once.

    The saved-recording test can't show this: a recording plays back instantly, so
    it shows the answer comes in many pieces but not that the pieces really arrived
    one at a time. Here we call the real API and note when each ``content`` piece
    arrives, then check:

    (a) more than one piece arrived;
    (b) the joined text is a real, non-empty answer;
    (c) exactly one ``done`` event ends it;
    (d) the main check — the gap between the first and last piece clears a small,
        safe floor, showing the pieces trickled in over time rather than being
        chopped off one already-finished answer in the same instant.

    Needs a real key; runs in the daily real-API job (see the top of this file) and
    skips itself in the normal no-key tests.
    """
    agent = build_agent(
        AgentSpec(
            name="assistant",
            engine="langgraph",
            model="openai/gpt-4o-mini",
            prompt="You are a concise assistant.",
            streaming=True,
        )
    )

    timestamps: list[float] = []
    events = []
    async for event in agent.stream(
        "Write two sentences (~40 words) about why the ocean is salty."
    ):
        if event.type == "content":
            timestamps.append(time.monotonic())
        events.append(event)

    content = [e for e in events if e.type == "content"]
    assert len(content) > 1, (
        f"expected multiple content chunks from a real streaming model, got "
        f"{len(content)} — a single chunk means the model did not stream tokens"
    )

    answer = "".join(e.data for e in content)
    assert answer.strip() != "", "reassembled streamed answer was empty"

    assert events[-1].type == "done"
    assert sum(1 for e in events if e.type == "done") == 1

    # The anti-buffering assertion. Genuine over-the-wire streaming spreads the
    # chunk arrivals across real network latency; buffer-then-rechunk collapses them
    # into ~one instant.
    spread = timestamps[-1] - timestamps[0]
    assert spread > MIN_ARRIVAL_SPREAD_S, (
        f"streamed {len(content)} chunks but they arrived within {spread:.4f}s of "
        f"each other (floor is {MIN_ARRIVAL_SPREAD_S}s) → looks buffered-then-"
        f"rechunked, not real incremental network streaming. The provider stream "
        f"should trickle tokens in over time; a ~0s spread means the whole answer "
        f"was buffered and then split locally."
    )
