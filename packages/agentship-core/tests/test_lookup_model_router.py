"""Tests for the LookupModelRouter models (C3.1) and pick() (C3.2)."""

from __future__ import annotations

import inspect

import pytest
from agentship.primitives.model_router import (
    RouterTable,
    TaskHint,
)
from pydantic import ValidationError


class TestTaskHint:
    def test_all_fields_optional(self) -> None:
        """A bare TaskHint carries no preference — every field defaults to None."""
        hint = TaskHint()
        assert hint.tier is None
        assert hint.model is None
        assert hint.modality is None

    def test_accepts_the_three_tiers(self) -> None:
        """`tier` accepts exactly cheap/balanced/strong."""
        for tier in ("cheap", "balanced", "strong"):
            assert TaskHint(tier=tier).tier == tier

    def test_rejects_unknown_tier(self) -> None:
        """An out-of-enum tier is a loud validation error."""
        with pytest.raises(ValidationError):
            TaskHint(tier="turbo")

    def test_carries_explicit_model_override(self) -> None:
        """A per-node override (e.g. cfg.classify.model) rides on `model`."""
        assert TaskHint(model="openai/gpt-4o-mini").model == "openai/gpt-4o-mini"

    def test_rejects_unknown_field(self) -> None:
        """TaskHint is extra=forbid."""
        with pytest.raises(ValidationError):
            TaskHint(bogus=1)


class TestRouterTable:
    def test_maps_tiers_to_model_ids(self) -> None:
        """A table resolves each tier to a concrete model id."""
        table = RouterTable(
            tiers={"cheap": "openai/gpt-4o-mini", "strong": "openai/gpt-4o"},
            default="openai/gpt-4o-mini",
        )
        assert table.tiers["cheap"] == "openai/gpt-4o-mini"
        assert table.default == "openai/gpt-4o-mini"

    def test_defaults_to_empty(self) -> None:
        """An empty table is valid — pick() then falls through to spec.model."""
        table = RouterTable()
        assert table.tiers == {}
        assert table.default is None

    def test_rejects_unknown_tier_key(self) -> None:
        """Tier keys are constrained to the enum; a stray key is rejected."""
        with pytest.raises(ValidationError):
            RouterTable(tiers={"turbo": "openai/gpt-4o"})

    def test_rejects_unknown_field(self) -> None:
        """RouterTable is extra=forbid."""
        with pytest.raises(ValidationError):
            RouterTable(bogus=1)


def test_models_have_docstrings() -> None:
    """Operating model: every new class carries a docstring."""
    assert inspect.getdoc(TaskHint)
    assert inspect.getdoc(RouterTable)
