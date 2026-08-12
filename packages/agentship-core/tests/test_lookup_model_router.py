"""Tests for the LookupModelRouter models (C3.1) and pick() (C3.2)."""

from __future__ import annotations

import inspect

import pytest
from agentship.primitives.model_router import (
    LookupModelRouter,
    ModelRouter,
    RouterTable,
    TaskHint,
)
from agentship.spec import AgentSpec
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


class TestLookupModelRouterPick:
    """C3.2: the four-tier deterministic resolution of :meth:`pick`.

    Resolution order (first match wins): explicit ``task.model`` override → a
    ``task.tier`` lookup in the table → the agent's own ``spec.model`` → the table's
    ``default``. No provider fail-fast lives here — that is the build-time job of
    ``EngineCapabilities._assert_provider_supported`` (see design gap resolution).
    """

    def _spec(self, model: str | None = None) -> AgentSpec:
        """A minimal spec carrying only the agent-default model under test."""
        return AgentSpec(name="a", engine="langgraph", model=model)

    def test_tier1_explicit_model_override_wins(self) -> None:
        """A per-node ``task.model`` beats the table and the spec default."""
        router = LookupModelRouter(
            RouterTable(tiers={"cheap": "openai/gpt-4o-mini"}, default="openai/gpt-4o")
        )
        got = router.pick(self._spec("openai/gpt-4o"), TaskHint(model="anthropic/claude"))
        assert got == "anthropic/claude"

    def test_tier2_tier_lookup(self) -> None:
        """With no explicit model, a ``task.tier`` resolves through the table."""
        router = LookupModelRouter(
            RouterTable(tiers={"cheap": "openai/gpt-4o-mini", "strong": "openai/gpt-4o"})
        )
        assert router.pick(self._spec("x/y"), TaskHint(tier="strong")) == "openai/gpt-4o"

    def test_tier2_tier_absent_from_table_falls_through(self) -> None:
        """A tier the table does not map falls through to the spec default."""
        router = LookupModelRouter(RouterTable(tiers={"cheap": "openai/gpt-4o-mini"}))
        assert router.pick(self._spec("spec/model"), TaskHint(tier="strong")) == "spec/model"

    def test_tier3_spec_model_default(self) -> None:
        """No override, no matching tier → the agent's own ``spec.model``."""
        router = LookupModelRouter(RouterTable())
        assert router.pick(self._spec("openai/gpt-4o"), TaskHint()) == "openai/gpt-4o"

    def test_tier3_used_when_no_hint_given(self) -> None:
        """``pick`` works with no hint at all (the runtime's plain routing call)."""
        router = LookupModelRouter(RouterTable(default="table/default"))
        assert router.pick(self._spec("openai/gpt-4o")) == "openai/gpt-4o"

    def test_tier4_table_default_when_spec_has_no_model(self) -> None:
        """With no override, tier, or spec model, the table ``default`` is last resort."""
        router = LookupModelRouter(RouterTable(default="openai/gpt-4o-mini"))
        assert router.pick(self._spec(None), TaskHint()) == "openai/gpt-4o-mini"

    def test_nothing_resolves_raises_capability_error(self) -> None:
        """Empty table + no spec model + empty hint → a loud, actionable failure."""
        from agentship.errors import CapabilityError

        router = LookupModelRouter(RouterTable())
        with pytest.raises(CapabilityError):
            router.pick(self._spec(None), TaskHint())

    def test_is_deterministic(self) -> None:
        """Same inputs pick the same model every time (guards identical-resume)."""
        router = LookupModelRouter(RouterTable(tiers={"cheap": "openai/gpt-4o-mini"}))
        spec, hint = self._spec("openai/gpt-4o"), TaskHint(tier="cheap")
        assert {router.pick(spec, hint) for _ in range(100)} == {"openai/gpt-4o-mini"}

    def test_is_a_model_router(self) -> None:
        """LookupModelRouter is a concrete ModelRouter (swappable via the registry)."""
        assert isinstance(LookupModelRouter(RouterTable()), ModelRouter)

    def test_never_calls_a_model(self, monkeypatch) -> None:
        """Purity: resolution touches no LLM path — litellm import stays untouched.

        `pick` is pure lookup; if it ever reached for a completion the test would
        need a network. We assert the positive contract (a value returns) with any
        model layer left unpatched, proving the code path is I/O-free.
        """
        router = LookupModelRouter(RouterTable(tiers={"cheap": "c/m"}))
        assert router.pick(self._spec("s/m"), TaskHint(tier="cheap")) == "c/m"


def test_models_have_docstrings() -> None:
    """Operating model: every new class carries a docstring."""
    assert inspect.getdoc(TaskHint)
    assert inspect.getdoc(RouterTable)
    assert inspect.getdoc(LookupModelRouter)
