"""Tests for the ConflictResolver primitive's models (C2.1) and resolve() (C2.2)."""

from __future__ import annotations

import inspect

import pytest
import yaml
from agentship.primitives.conflict_resolver import (
    ConflictPolicy,
    ConflictResolver,
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


def _r(name: str, *, output: dict | None = None, confidence=None, error=None) -> SpecialistResult:
    """Build a SpecialistResult with sensible defaults for terse test cases."""
    return {
        "name": name,
        "output": output if output is not None else {"v": name},
        "confidence": confidence,
        "error": error,
    }


class TestResolve:
    def test_picks_highest_priority_survivor(self) -> None:
        """The winner is the survivor earliest in the priority list."""
        resolver = ConflictResolver(ConflictPolicy(priority=["a", "b", "c"]))
        out = resolver.resolve([_r("c"), _r("a"), _r("b")])
        assert out["winner"] == "a"
        assert out["output"] == {"v": "a"}

    def test_returns_considered_and_dropped(self) -> None:
        """Survivors are `considered`; errored specialists are `dropped`, not winners."""
        resolver = ConflictResolver(ConflictPolicy(priority=["a", "b"]))
        out = resolver.resolve([_r("a", error="boom"), _r("b")])
        assert out["winner"] == "b"
        assert out["considered"] == ["b"]
        assert out["dropped"] == ["a"]

    def test_no_survivors_reports_status(self) -> None:
        """When every specialist errored, there is no winner — a typed status instead."""
        resolver = ConflictResolver(ConflictPolicy(priority=["a"]))
        out = resolver.resolve([_r("a", error="boom")])
        assert out["winner"] is None
        assert out["status"] == "no_specialist_succeeded"

    def test_empty_input_reports_status(self) -> None:
        """No specialists at all resolves to the same no-survivor status."""
        resolver = ConflictResolver(ConflictPolicy(priority=["a"]))
        out = resolver.resolve([])
        assert out["winner"] is None
        assert out["status"] == "no_specialist_succeeded"

    def test_unknown_names_sort_last_stably(self) -> None:
        """Names absent from the priority list rank last, in original order."""
        resolver = ConflictResolver(ConflictPolicy(priority=["known"]))
        out = resolver.resolve([_r("x"), _r("y")])
        # Neither is in priority; first_by_priority keeps original order → x wins.
        assert out["winner"] == "x"

    def test_on_tie_highest_confidence(self) -> None:
        """Among equal-rank survivors, highest_confidence prefers the larger score."""
        policy = ConflictPolicy(priority=["known"], on_tie="highest_confidence")
        resolver = ConflictResolver(policy)
        out = resolver.resolve([_r("x", confidence=0.2), _r("y", confidence=0.9)])
        assert out["winner"] == "y"

    def test_on_tie_first_by_priority_ignores_confidence(self) -> None:
        """The default tie-break keeps list order regardless of confidence."""
        resolver = ConflictResolver(ConflictPolicy(priority=["known"]))
        out = resolver.resolve([_r("x", confidence=0.2), _r("y", confidence=0.9)])
        assert out["winner"] == "x"

    def test_highest_confidence_treats_none_as_lowest(self) -> None:
        """A missing confidence never beats a real score under highest_confidence."""
        policy = ConflictPolicy(priority=["known"], on_tie="highest_confidence")
        resolver = ConflictResolver(policy)
        out = resolver.resolve([_r("x", confidence=None), _r("y", confidence=0.1)])
        assert out["winner"] == "y"

    def test_priority_beats_confidence(self) -> None:
        """Priority rank dominates: a higher-priority survivor wins despite low score."""
        policy = ConflictPolicy(priority=["a", "b"], on_tie="highest_confidence")
        resolver = ConflictResolver(policy)
        out = resolver.resolve([_r("a", confidence=0.1), _r("b", confidence=0.99)])
        assert out["winner"] == "a"
