"""Pre-written LangGraph build bodies selected by ``AgentSpec.template``.

A *template* is a build body the engine generates so an author writes little or no
Python. Each template exposes a ``build_<name>(spec)`` factory returning a
``build_graph(model, tools)`` callable in the same shape a custom
:class:`~agentship_langgraph.agent.LangGraphAgent` would implement — so the engine
drives templated and custom agents through the identical seam.

``single``, ``graph``, and ``deepagents`` all ship now. The engine dispatches on
``spec.template`` via :func:`resolve_template`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .deepagents_tpl import build_deepagents_template
from .graph import build_graph_template
from .single import build_single

if TYPE_CHECKING:
    from agentship.spec import AgentSpec
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.tools import BaseTool


class BuildGraph(Protocol):
    """The build-body callable shape every template (and custom agent) provides."""

    def __call__(self, model: BaseChatModel, tools: list[BaseTool]) -> object:
        """Return a native LangGraph ``StateGraph`` (or a compiled graph)."""
        ...


#: Maps a template name to the factory that produces its ``build_graph`` body.
_TEMPLATES = {
    "single": build_single,
    "graph": build_graph_template,
    "deepagents": build_deepagents_template,
}


def resolve_template(spec: AgentSpec) -> BuildGraph | None:
    """Return the ``build_graph`` body for ``spec.template``, or ``None`` if unset.

    ``None`` means the spec asks for no template (the engine uses its own default
    build body). A template name that has no shipped body yet (``graph``,
    ``deepagents``) raises :class:`NotImplementedError` naming the missing template,
    so the gap is a loud, actionable error rather than a silent fall-through to the
    wrong build path.
    """
    if spec.template is None:
        return None
    factory = _TEMPLATES.get(spec.template)
    if factory is None:
        raise NotImplementedError(
            f"template {spec.template!r} is not implemented yet on the langgraph "
            f"engine (available: {sorted(_TEMPLATES)}) — it lands in a later wave."
        )
    return factory(spec)


__all__ = [
    "BuildGraph",
    "build_deepagents_template",
    "build_graph_template",
    "build_single",
    "resolve_template",
]
