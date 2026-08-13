"""The triage supervisor — the Phase 02 killer demo, authored via a ``code:`` factory.

A durable multi-agent supervisor that classifies an incoming request and routes it to the right
specialist. ``build_triage_supervisor`` builds three real specialist agents (billing, clinical,
general) and hands them to a :class:`SupervisorAgent` along with the routing config. The supervisor
classifies with ``gpt-4o-mini``, dispatches to the matching specialist, and resolves the answer —
all checkpointed (``durability: checkpoint``) so a ``kill -9`` mid-run resumes to an identical
result. There is no global registry: the specialists are a plain ``{name: agent}`` dict this factory
builds and closes over.

Run it (with a key set), from the demo repo root::

    agentship run agents/triage/triage.yaml --input "My invoice looks wrong — who handles payments?"
"""

from __future__ import annotations

from agentship import build_agent
from agentship.spec import AgentSpec
from agentship_langgraph.templates.graph_config import GraphConfig
from agentship_langgraph.templates.graph_supervisor import SupervisorAgent

_MODEL = "openai/gpt-4o-mini"

_SPECIALISTS = {
    "billing_specialist": "You are a billing specialist. Answer billing, invoice, and payment "
    "questions concisely and point the user to the right next step.",
    "clinical_specialist": "You are a clinical information specialist. Answer general health and "
    "symptom questions concisely. Always add that this is not medical advice.",
    "faq_specialist": "You are a general help specialist. Answer general questions about the "
    "service concisely.",
}

_CONFIG = {
    "classify": {"model": _MODEL, "intents": ["billing", "clinical", "general"]},
    "routing": {
        "billing": {"specialists": ["billing_specialist"], "strategy": "single"},
        "clinical": {"specialists": ["clinical_specialist"], "strategy": "single"},
        "general": {"specialists": ["faq_specialist"], "strategy": "single"},
        "_default": {"specialists": ["faq_specialist"], "strategy": "single"},
    },
    "conflict_resolver": {
        "priority": ["billing_specialist", "clinical_specialist", "faq_specialist"]
    },
    "retry": {"max_attempts": 2, "on": ["timeout", "specialist_error"]},
}


def build_triage_supervisor() -> SupervisorAgent:
    """Build the durable triage supervisor with its three specialists wired in."""
    specialists = {
        name: build_agent(
            AgentSpec(name=name, engine="langgraph", model=_MODEL, prompt=prompt)
        )
        for name, prompt in _SPECIALISTS.items()
    }
    config = GraphConfig.model_validate(_CONFIG)
    spec = AgentSpec(name="triage", engine="langgraph", model=_MODEL, durability="checkpoint")
    return SupervisorAgent(spec, config=config, specialists=specialists)
