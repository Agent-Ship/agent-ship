"""Live proof: real ``gpt-4o-mini`` streaming yields multiple token chunks.

This is the missing live proof for the *streaming* path. ``test_live_model.py``
proves ``run`` reaches a real provider; this proves ``stream`` does too — and that
it genuinely streams **token by token**, not as one whole message. ``@pytest.mark.vcr``
records the streaming HTTP round-trip once (auth header redacted by the shared
``vcr_config`` in the repo-root ``conftest.py``) into ``tests/cassettes/`` and
replays it keyless on every later run, so CI verifies real token streaming without
secrets.

Why this test exists: ``ChatLiteLLM`` returns a single whole ``AIMessage`` unless
built with ``streaming=True``. The engine's ``stream`` keeps only ``AIMessageChunk``
tokens, so a non-streaming model silently yielded **zero** content events against a
real provider — a bug masked offline by ``FakeListChatModel`` (which chunks
regardless). This test fails (0 content events) before ``resolve_model`` sets
``streaming=True`` and passes after.

Recording is done deliberately, once:

    set -a; source .env; set +a
    pytest tests/test_streaming_live.py --record-mode=once
"""

from __future__ import annotations

import os

import litellm
import pytest
from agentship.runtime import build_agent
from agentship.spec import AgentSpec

# LiteLLM's async path defaults to an aiohttp transport that vcrpy (httpx/urllib3
# based) cannot intercept. Forcing the plain httpx transport lets the cassette
# both record and replay the real streaming round-trip.
litellm.disable_aiohttp_transport = True

# Keep the cost map local so no extra HTTP fetch pollutes the cassette.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")


@pytest.fixture
def openai_key_for_replay(monkeypatch):
    """Ensure an OpenAI key is set so the SDK client constructs during replay.

    The OpenAI SDK refuses to build a client without a key — even to replay a
    recorded response. On keyless replay (CI) inject a harmless placeholder; VCR
    matches on method+host+path+body, not the (redacted) auth header, so replay is
    unaffected. When a real key *is* present (recording), it is left untouched.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder-for-replay")


@pytest.mark.vcr
async def test_real_gpt_4o_mini_streams_multiple_token_chunks(openai_key_for_replay):
    """A real gpt-4o-mini turn streams as multiple content chunks, then one done.

    Asserts the streaming contract against a real provider (replayed via cassette):
    (a) more than one ``content`` event arrives — genuine multi-chunk token
    streaming, not a single whole message; (b) the reassembled text is a non-empty
    answer; (c) exactly one terminal ``done`` event ends the stream.
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
    events = [e async for e in agent.stream("Name the 8 planets, comma-separated.")]

    content = [e for e in events if e.type == "content"]
    # Genuine token-level streaming: the answer arrives across multiple chunks.
    assert len(content) > 1, (
        f"expected multiple content chunks from a real streaming model, "
        f"got {len(content)} (0 means the model streamed one whole message, "
        f"not tokens — resolve_model must set streaming=True)"
    )

    answer = "".join(e.data for e in content)
    assert answer.strip() != ""

    assert events[-1].type == "done"
    assert sum(1 for e in events if e.type == "done") == 1
