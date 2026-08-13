"""The triage supervisor — the Phase 02 killer demo, authored via a ``code:`` factory.

A durable multi-agent supervisor that classifies an incoming request and routes it to the right
specialist. Each specialist is authored as **its own YAML file** under ``specialists/`` (a plain
``template: single`` agent, runnable on its own); the factory loads them by path and hands them to a
:class:`SupervisorAgent` along with the routing config. The supervisor classifies with
``gpt-4o-mini``, dispatches to the matching specialist, and resolves the answer — all checkpointed
(``durability: checkpoint``) so a ``kill -9`` mid-run resumes to an identical result. There is no
global registry: the specialists are a plain ``{name: agent}`` dict this factory builds from the
YAML files and closes over.

This mirrors the old agent-ship layout (a supervisor + a folder of sub-agent YAMLs), so sub-agents
stay declarative and reusable rather than hard-coded in Python.

Run it (with a key set), from the demo repo root::

    agentship run agents/triage/triage.yaml --input "My invoice looks wrong — who handles payments?"
"""

from __future__ import annotations

from pathlib import Path

from agentship import build_agent
from agentship.spec import AgentSpec
from agentship_langgraph.templates.graph_config import GraphConfig
from agentship_langgraph.templates.graph_supervisor import SupervisorAgent

_MODEL = "openai/gpt-4o-mini"

#: The specialist sub-agents, each authored as its own YAML file next to this module.
#: The supervisor dispatches to them by name; each is also runnable standalone.
_SPECIALISTS_DIR = Path(__file__).resolve().parent / "specialists"
_SPECIALIST_FILES = {
    "billing_specialist": _SPECIALISTS_DIR / "billing.yaml",
    "clinical_specialist": _SPECIALISTS_DIR / "clinical.yaml",
    "faq_specialist": _SPECIALISTS_DIR / "faq.yaml",
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


# The panel config fans EVERY request out to all three specialists **concurrently**
# (strategy: parallel), then the ConflictResolver merges their competing answers by
# priority (billing > clinical > faq). This is the multi-agent fan-out demo: several
# real sub-agents run at once and their outputs are reconciled deterministically.
_PANEL_CONFIG = {
    "classify": {"model": _MODEL, "intents": ["billing", "clinical", "general"]},
    "routing": {
        "_default": {
            "specialists": ["billing_specialist", "clinical_specialist", "faq_specialist"],
            "strategy": "parallel",
        }
    },
    "conflict_resolver": {
        "priority": ["billing_specialist", "clinical_specialist", "faq_specialist"]
    },
    "retry": {"max_attempts": 2, "on": ["timeout", "specialist_error"]},
}


def _build_specialists() -> dict[str, object]:
    """Load each specialist sub-agent from its own YAML file (each a full, independent agent)."""
    return {name: build_agent(str(path)) for name, path in _SPECIALIST_FILES.items()}


def build_triage_supervisor() -> SupervisorAgent:
    """Build the durable triage supervisor: classify → route to ONE specialist → resolve."""
    config = GraphConfig.model_validate(_CONFIG)
    spec = AgentSpec(name="triage", engine="langgraph", model=_MODEL, durability="checkpoint")
    return SupervisorAgent(spec, config=config, specialists=_build_specialists())


def build_triage_panel() -> SupervisorAgent:
    """Build the fan-out panel: dispatch to ALL three specialists in parallel, then resolve.

    Same three sub-agents as :func:`build_triage_supervisor`, but every request is fanned out to all
    of them concurrently (``strategy: parallel``) and the ``ConflictResolver`` picks the winner by
    priority — the clearest demonstration that multiple real sub-agents run and are merged.
    """
    config = GraphConfig.model_validate(_PANEL_CONFIG)
    spec = AgentSpec(name="triage-panel", engine="langgraph", model=_MODEL, durability="checkpoint")
    return SupervisorAgent(spec, config=config, specialists=_build_specialists())
