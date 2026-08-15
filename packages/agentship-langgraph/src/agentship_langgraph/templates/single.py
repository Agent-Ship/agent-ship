"""The ``single`` template: one model + tools, generated with zero author code.

``template: single`` yields a runnable agent from the YAML/spec alone — the whole
build body is generated here via LangGraph's prebuilt ReAct agent
(``create_react_agent(model, tools, prompt)``). This is the explicit, single-node
ReAct build path made a first-class template, so the simplest useful agent needs no
Python.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.prebuilt import create_react_agent

if TYPE_CHECKING:
    from agentship.spec import AgentSpec
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.tools import BaseTool
    from langgraph.graph.state import CompiledStateGraph


def build_single(spec: AgentSpec):
    """Return a ``build_graph(model, tools)`` body for the ``single`` template.

    The returned callable produces a prebuilt ReAct agent over the wired ``model``
    and ``tools``, using ``spec.prompt`` as the system prompt. ``create_react_agent``
    returns an already-compiled graph; the engine detects that and drives it as-is
    (no double compile). The factory closes over ``spec`` only for the prompt — the
    model and tools are supplied by the engine at build time.
    """

    def build_graph(
        model: BaseChatModel, tools: list[BaseTool]
    ) -> CompiledStateGraph:
        """Build the prebuilt ReAct graph over the model, tools, and skill-augmented prompt."""
        from agentship.skills import render_agent_prompt

        prompt = render_agent_prompt(spec.prompt, spec.skills)
        return create_react_agent(model, tools=tools, prompt=prompt)

    return build_graph
