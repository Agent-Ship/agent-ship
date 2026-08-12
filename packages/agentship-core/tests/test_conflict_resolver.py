"""Tests for the ConflictResolver primitive's models (C2.1) and resolve() (C2.2)."""

from __future__ import annotations

import inspect

import pytest
import yaml
from agentship.primitives.conflict_resolver import (
    ConflictPolicy,
    SpecialistResult,
)
from pydantic import ValidationError

# The `conflict_resolver:` block a demo agent's YAML carries (design §4.1).
DEMO_YAML = """
conflict_resolver:
  priority: [clinical_safety, drug_specialist, symptom_specialist, faq_specialist]
  on_tie: highest_confidence
"""


class TestConflictPolicyModel:
    def test_validates_demo_yaml_block(self) -> None:
        """The demo YAML `conflict_resolver` block parses into a ConflictPolicy."""
        block = yaml.safe_load(DEMO_YAML)["conflict_resolver"]
        policy = ConflictPolicy.model_validate(block)
        assert policy.priority == [
            "clinical_safety",
            "drug_specialist",
            "symptom_specialist",
            "faq_specialist",
        ]
        assert policy.on_tie == "highest_confidence"

    def test_on_tie_defaults_to_first_by_priority(self) -> None:
        """`on_tie` is optional and defaults to deterministic first_by_priority."""
        policy = ConflictPolicy.model_validate({"priority": ["a", "b"]})
        assert policy.on_tie == "first_by_priority"

    def test_rejects_unknown_field(self) -> None:
        """Policies are extra=forbid — a stray key is a loud error, not a silent typo."""
        with pytest.raises(ValidationError):
            ConflictPolicy.model_validate({"priority": ["a"], "bogus": 1})

    def test_rejects_bad_on_tie_value(self) -> None:
        """`on_tie` is a closed Literal; an unknown strategy is rejected."""
        with pytest.raises(ValidationError):
            ConflictPolicy.model_validate({"priority": ["a"], "on_tie": "random"})


class TestSpecialistResultModel:
    def test_typeddict_has_expected_keys(self) -> None:
        """SpecialistResult carries name, output, confidence and error."""
        annotations = SpecialistResult.__annotations__
        assert set(annotations) == {"name", "output", "confidence", "error"}

    def test_constructs_as_plain_dict(self) -> None:
        """A SpecialistResult is a TypedDict — usable as an ordinary dict literal."""
        result: SpecialistResult = {
            "name": "faq_specialist",
            "output": {"answer": "42"},
            "confidence": 0.9,
            "error": None,
        }
        assert result["name"] == "faq_specialist"


def test_both_models_have_docstrings() -> None:
    """Operating model: every new class carries a docstring."""
    assert inspect.getdoc(ConflictPolicy)
    assert inspect.getdoc(SpecialistResult)
