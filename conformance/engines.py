"""Per-engine offline harness so positive conformance cells never touch the network.

A positive cell builds and drives a real agent on a real engine. Engines that need
a model (LangGraph → LiteLLM) would otherwise reach the network, so this module
provides one thing per engine: an :func:`offline` context manager that, for the
duration of a cell, swaps in a deterministic fake model (or does nothing, for a
model-free engine like ``echo``).

**Extension point:** to make a new engine's cells run offline, register an
``offline`` provider for it in :data:`OFFLINE_HARNESSES`. An engine that needs no
patching (no model, no network) needs no entry — the default is a no-op.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext


@contextmanager
def _langgraph_offline() -> Iterator[None]:
    """Patch the LangGraph engine's model seam to a deterministic fake, for one cell.

    Replaces :func:`agentship_langgraph.models.resolve_model` with a factory that
    returns a token-streaming ``FakeListChatModel``, so the engine compiles and
    drives its graph with zero network calls. Restored on exit.
    """
    import agentship_langgraph.models as models_module
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    original = models_module.resolve_model
    fake = FakeListChatModel(responses=["conformance ok, streamed in pieces."])
    models_module.resolve_model = lambda *args, **kwargs: fake  # type: ignore[assignment]
    try:
        yield
    finally:
        models_module.resolve_model = original  # type: ignore[assignment]


#: Maps an engine name to a zero-arg context manager that makes its cells offline.
#: An engine absent from this map runs with no patching (see :func:`offline`). This
#: is the only edit needed to bring a new model-backed engine into the matrix.
OFFLINE_HARNESSES: dict[str, Callable[[], AbstractContextManager[None]]] = {
    "langgraph": _langgraph_offline,
}


@contextmanager
def offline(engine_name: str) -> Iterator[None]:
    """Enter the offline harness for ``engine_name`` (a no-op if it needs none).

    Model-free engines (``echo``) have no entry and run unpatched; model-backed
    engines get their model seam swapped for a fake so every positive cell stays
    fully offline.
    """
    harness = OFFLINE_HARNESSES.get(engine_name)
    with ExitStack() as stack:
        stack.enter_context(harness() if harness is not None else nullcontext())
        yield
