"""C1.1: ``GraphConfig`` — the typed parse of a supervisor's ``graph:`` YAML block.

The ``graph`` template is config-driven: classify intents, a routing lookup table, the conflict
policy, the retry cap, and the HITL write-list all come from YAML. ``GraphConfig`` validates that
block up front so a typo or a missing ``_default`` route fails at load with a clear message, not
mid-run. It reuses the core ``ConflictPolicy`` and dispatch ``Strategy`` so there is one definition
of each.
"""

from __future__ import annotations

import pytest
from agentship_langgraph.templates.graph_config import GraphConfig
from pydantic import ValidationError

_DEMO = {
    "classify": {"model": "openai/gpt-4o-mini", "intents": ["billing", "clinical", "general"]},
    "routing": {
        "billing": {"specialists": ["billing_specialist"], "strategy": "single"},
        "clinical": {
            "specialists": ["symptom_specialist", "drug_specialist"],
            "strategy": "parallel",
        },
        "general": {"specialists": ["faq_specialist"], "strategy": "single"},
        "_default": {"specialists": ["faq_specialist"], "strategy": "single"},
    },
    "conflict_resolver": {
        "priority": ["drug_specialist", "faq_specialist"],
        "on_tie": "highest_confidence",
    },
    "retry": {"max_attempts": 3, "on": ["timeout", "specialist_error"]},
    "hitl": {"confirm_before_write": True, "write_tools": ["schedule_appointment"]},
}


def test_validates_the_demo_graph_block():
    """The triage demo's graph config parses into a fully-populated GraphConfig."""
    cfg = GraphConfig.model_validate(_DEMO)
    assert cfg.classify.intents == ["billing", "clinical", "general"]
    assert cfg.routing["clinical"].strategy == "parallel"
    assert cfg.routing["clinical"].specialists == ["symptom_specialist", "drug_specialist"]
    assert cfg.conflict_resolver.on_tie == "highest_confidence"
    assert cfg.retry.max_attempts == 3
    assert cfg.hitl.write_tools == ["schedule_appointment"]


def test_missing_default_route_is_rejected():
    """A routing table with no '_default' fails validation — every intent must have a fallback."""
    bad = {**_DEMO, "routing": {"billing": {"specialists": ["b"], "strategy": "single"}}}
    with pytest.raises(ValidationError) as exc:
        GraphConfig.model_validate(bad)
    assert "_default" in str(exc.value)


def test_retry_and_hitl_default_when_omitted():
    """retry and hitl are optional — sensible defaults apply when the YAML omits them."""
    minimal = {
        "classify": _DEMO["classify"],
        "routing": {"_default": {"specialists": ["faq"], "strategy": "single"}},
        "conflict_resolver": {"priority": ["faq"]},
    }
    cfg = GraphConfig.model_validate(minimal)
    assert cfg.retry.max_attempts == 3
    assert cfg.retry.on == ["timeout", "specialist_error"]
    assert cfg.hitl.confirm_before_write is False


def test_unknown_strategy_is_rejected():
    """A route strategy outside {single, parallel, sequential} is a loud error."""
    bad = {
        **_DEMO,
        "routing": {"_default": {"specialists": ["x"], "strategy": "teleport"}},
    }
    with pytest.raises(ValidationError):
        GraphConfig.model_validate(bad)


def test_unknown_top_level_key_is_rejected():
    """A typo'd top-level key (extra='forbid') is caught, not silently ignored."""
    with pytest.raises(ValidationError):
        GraphConfig.model_validate({**_DEMO, "nonsense": 1})
