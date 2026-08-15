"""The ``autonomous`` template: one self-directing agent, zero author code.

``template: autonomous`` builds a *single* autonomous agent — it plans, calls its
own tools, and observes results in a loop until it decides it is done. It is a
thin wrapper over the `deepagents <https://pypi.org/project/deepagents/>`_ library's
``create_deep_agent`` (a planning/sub-agent/filesystem loop) over the wired
``model`` and ``tools``, using ``spec.prompt`` as the system prompt.

This is *not* AgentShip's multi-agent system — a coordinator routing to specialists
is the ``graph`` template / supervisor. ``autonomous`` is the archetype for "give one
agent tools and let it drive itself." The underlying library is pinned to
``deepagents==0.6.12`` (pre-1.0; see the langgraph package deps), and
:func:`deepagents_version_ok` powers an ``agentship doctor`` guard that flags a
drifted install before it fails at build.

**Scope of this wave (honest).** The template *builds* — ``create_deep_agent``
returns a compiled LangGraph graph the engine drives. Driving a full autonomous turn
needs a tool-calling-capable model (deepagents binds its own planning/filesystem
tools and runs a real agent loop), so the offline proof asserts the graph *builds and
compiles* against a fake model; exercising the full autonomous loop against a
scripted tool-calling model lands with tool execution (Phase 03). The import is done
lazily inside the build body so the rest of the engine never requires deepagents to
be installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agentship.spec import AgentSpec
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.tools import BaseTool

#: The deepagents version this template is written and tested against. The doctor
#: guard warns when the installed version differs, since deepagents is pre-1.0 and
#: its ``create_deep_agent`` signature can drift between releases.
PINNED_DEEPAGENTS_VERSION = "0.6.12"


def deepagents_version_ok() -> tuple[bool, str | None]:
    """Return ``(ok, installed_version)`` for the ``agentship doctor`` version guard.

    ``ok`` is ``True`` only when deepagents is installed *and* its version matches
    :data:`PINNED_DEEPAGENTS_VERSION`. When deepagents is not installed the installed
    version is ``None`` and ``ok`` is ``False`` (the ``autonomous`` template cannot
    build). When a *different* version is installed ``ok`` is ``False`` and the
    installed version is returned so ``doctor`` can name the drift — deepagents is
    pre-1.0, so a signature change is a real risk this flags early.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            installed = version("deepagents")
        except PackageNotFoundError:
            return (False, None)
    except ImportError:  # pragma: no cover - importlib.metadata is stdlib on 3.13
        return (False, None)
    return (installed == PINNED_DEEPAGENTS_VERSION, installed)


def build_autonomous_template(spec: AgentSpec):
    """Return a ``build_graph(model, tools)`` body for the ``autonomous`` template.

    The returned callable configures ``create_deep_agent`` over the wired ``model``
    and ``tools`` with ``spec.prompt`` as the system prompt, returning the compiled
    LangGraph graph deepagents produces (the engine detects it is already compiled
    and drives it as-is). ``deepagents`` is imported lazily inside the body so the
    engine imports fine even when the optional dependency is absent — an
    ``autonomous`` spec then fails at build with a clear ``ImportError`` (surfaced by
    ``agentship doctor`` ahead of time via :func:`deepagents_version_ok`).
    """

    def build_graph(model: BaseChatModel, tools: list[BaseTool]):
        """Configure the autonomous agent over the wired model, tools, and skill prompt."""
        from agentship.skills import render_agent_prompt
        from deepagents import create_deep_agent

        return create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=render_agent_prompt(spec.prompt, spec.skills) or None,
        )

    return build_graph
