"""Tests for the authoring layer: YAML loading, extra-key rejection, and ``code:``."""

from __future__ import annotations

import textwrap

import pytest
from agentship.errors import SpecError
from agentship.spec import AgentSpec, MemberSpec, ModelParams, load_spec, resolve_code


def test_member_ref_and_description_load_from_yaml(tmp_path):
    """A member may be declared by a `ref:` to a sub-agent YAML plus a routing `description:`."""
    f = tmp_path / "team.yaml"
    f.write_text(
        textwrap.dedent(
            """
            name: team
            engine: langgraph
            model: openai/gpt-4o-mini
            members:
              - name: billing
                ref: specialists/billing.yaml
                description: billing, invoices, and payments
            """
        )
    )
    spec = load_spec(f)
    member = spec.members[0]
    assert member.name == "billing"
    assert member.description == "billing, invoices, and payments"
    # The ref is resolved to an absolute path relative to the team YAML's directory.
    assert member.ref == str((tmp_path / "specialists" / "billing.yaml").resolve())


def test_member_ref_and_inline_prompt_are_mutually_exclusive():
    """A member is authored EITHER by `ref:` (its YAML brings the prompt) OR inline `prompt:`."""
    with pytest.raises(SpecError) as exc:
        MemberSpec(name="x", ref="a.yaml", prompt="inline")
    assert "ref" in str(exc.value) and "prompt" in str(exc.value)


def test_member_defaults_have_no_ref_or_description():
    """A bare member (name only) keeps ref/description as None — nothing invented."""
    member = MemberSpec(name="m1")
    assert member.ref is None
    assert member.description is None


def test_load_spec_parses_yaml(tmp_path):
    """A well-formed YAML file loads into an AgentSpec with its fields populated."""
    f = tmp_path / "a.yaml"
    f.write_text("name: hello\nengine: echo\nprompt: hi there\n")
    spec = load_spec(f)
    assert isinstance(spec, AgentSpec)
    assert spec.name == "hello"
    assert spec.engine == "echo"
    assert spec.prompt == "hi there"


def test_unknown_key_is_rejected(tmp_path):
    """extra='forbid' means an unknown field is a loud SpecError, not a silent drop."""
    f = tmp_path / "a.yaml"
    f.write_text("name: hello\nengine: echo\nnonsense: 42\n")
    with pytest.raises(SpecError) as exc:
        load_spec(f)
    # The real mechanism: the offending key name appears in the error.
    assert "nonsense" in str(exc.value)


def test_missing_file_raises_spec_error(tmp_path):
    """A missing spec file surfaces as an actionable SpecError."""
    with pytest.raises(SpecError):
        load_spec(tmp_path / "does-not-exist.yaml")


def test_non_mapping_yaml_raises(tmp_path):
    """A YAML document that isn't a mapping is rejected with a clear message."""
    f = tmp_path / "a.yaml"
    f.write_text("- just\n- a\n- list\n")
    with pytest.raises(SpecError):
        load_spec(f)


def test_params_block_loads_and_populates_model_params(tmp_path):
    """A ``params:`` block loads into a ModelParams with its fields populated."""
    f = tmp_path / "a.yaml"
    f.write_text(
        textwrap.dedent(
            """
            name: tuned
            engine: langgraph
            model: openai/gpt-4o-mini
            params:
              temperature: 0.2
              max_tokens: 256
              api_base: http://localhost:11434
              timeout: 30
            """
        )
    )
    spec = load_spec(f)
    assert isinstance(spec.params, ModelParams)
    assert spec.params.temperature == 0.2
    assert spec.params.max_tokens == 256
    assert spec.params.api_base == "http://localhost:11434"
    assert spec.params.timeout == 30


def test_params_unknown_key_is_rejected(tmp_path):
    """extra='forbid' on ModelParams: an unknown param key is a loud SpecError."""
    f = tmp_path / "a.yaml"
    f.write_text(
        textwrap.dedent(
            """
            name: tuned
            engine: langgraph
            model: openai/gpt-4o-mini
            params:
              top_p: 0.9
            """
        )
    )
    with pytest.raises(SpecError) as exc:
        load_spec(f)
    # The offending key name appears in the error.
    assert "top_p" in str(exc.value)


def test_params_omitted_still_builds(tmp_path):
    """A spec with no ``params:`` block loads with params defaulting to None."""
    f = tmp_path / "a.yaml"
    f.write_text("name: hello\nengine: langgraph\nmodel: openai/gpt-4o-mini\n")
    spec = load_spec(f)
    assert spec.params is None


def test_params_bad_type_raises_spec_error(tmp_path):
    """A non-numeric temperature is a validation error surfaced as SpecError."""
    f = tmp_path / "a.yaml"
    f.write_text(
        textwrap.dedent(
            """
            name: tuned
            engine: langgraph
            model: openai/gpt-4o-mini
            params:
              temperature: hot
            """
        )
    )
    with pytest.raises(SpecError):
        load_spec(f)


def test_output_schema_and_durability_load_from_yaml(tmp_path):
    """The first-class ``output_schema`` ref and ``durability`` mode load from YAML."""
    f = tmp_path / "a.yaml"
    f.write_text(
        textwrap.dedent(
            """
            name: structured
            engine: echo
            output_schema: mypkg.models:Answer
            durability: checkpoint
            """
        )
    )
    spec = load_spec(f)
    assert spec.output_schema == "mypkg.models:Answer"
    assert spec.durability == "checkpoint"


def test_output_schema_and_durability_default_to_none(tmp_path):
    """With neither field set, output_schema is None and durability is 'none'."""
    f = tmp_path / "a.yaml"
    f.write_text("name: plain\nengine: echo\n")
    spec = load_spec(f)
    assert spec.output_schema is None
    assert spec.durability == "none"


def test_bad_durability_value_is_rejected(tmp_path):
    """An out-of-range durability value is a loud SpecError, not a silent accept."""
    f = tmp_path / "a.yaml"
    f.write_text("name: a\nengine: echo\ndurability: forever\n")
    with pytest.raises(SpecError):
        load_spec(f)


def test_durability_mode_defaults_to_async():
    """The runtime checkpoint-flush mode defaults to 'async' (good coverage, low latency)."""
    assert AgentSpec(name="a", engine="langgraph").durability_mode == "async"


def test_durability_mode_loads_from_yaml(tmp_path):
    """A demo/prod agent can set the stronger 'sync' flush mode via YAML."""
    f = tmp_path / "a.yaml"
    f.write_text(
        textwrap.dedent(
            """
            name: durable
            engine: langgraph
            durability: checkpoint
            durability_mode: sync
            """
        )
    )
    spec = load_spec(f)
    assert spec.durability == "checkpoint"
    assert spec.durability_mode == "sync"


def test_durability_mode_is_a_distinct_field_from_durability():
    """durability (none|checkpoint|workflow) and durability_mode (sync|async|exit) don't collide."""
    spec = AgentSpec(name="a", engine="langgraph", durability="checkpoint", durability_mode="exit")
    assert spec.durability == "checkpoint" and spec.durability_mode == "exit"


def test_bad_durability_mode_value_is_rejected(tmp_path):
    """An out-of-range durability_mode is a loud SpecError, not a silent accept."""
    f = tmp_path / "a.yaml"
    f.write_text("name: a\nengine: langgraph\ndurability_mode: turbo\n")
    with pytest.raises(SpecError):
        load_spec(f)


def test_template_and_tools_round_trip_from_yaml(tmp_path):
    """A ``template:`` + ``tools:`` YAML round-trips into the spec's fields."""
    f = tmp_path / "a.yaml"
    f.write_text(
        textwrap.dedent(
            """
            name: quickstart
            engine: langgraph
            template: single
            model: openai/gpt-4o-mini
            prompt: You are helpful.
            tools:
              - mcp:postgres
              - my.tools:search
            """
        )
    )
    spec = load_spec(f)
    assert spec.template == "single"
    assert spec.tools == ["mcp:postgres", "my.tools:search"]


def test_template_defaults_to_none(tmp_path):
    """With no ``template:`` set, the field defaults to None (engine's own default)."""
    f = tmp_path / "a.yaml"
    f.write_text("name: a\nengine: langgraph\nmodel: openai/gpt-4o-mini\n")
    spec = load_spec(f)
    assert spec.template is None
    assert spec.tools is None


def test_bad_template_value_is_rejected(tmp_path):
    """A template outside the closed set is a loud SpecError, not a silent accept."""
    f = tmp_path / "a.yaml"
    f.write_text("name: a\nengine: langgraph\ntemplate: wizard\n")
    with pytest.raises(SpecError):
        load_spec(f)


def test_deepagents_template_requires_langgraph_engine():
    """template 'deepagents' only exists on the langgraph engine — else SpecError."""
    with pytest.raises(SpecError) as exc:
        AgentSpec(name="a", engine="echo", template="deepagents")
    msg = str(exc.value).lower()
    assert "deepagents" in msg and "langgraph" in msg


def test_deepagents_template_on_langgraph_is_allowed():
    """template 'deepagents' is coherent on the langgraph engine (no error)."""
    spec = AgentSpec(name="a", engine="langgraph", template="deepagents", model="x")
    assert spec.template == "deepagents"


def test_template_and_code_are_mutually_exclusive():
    """Setting both ``template:`` and ``code:`` is contradictory — SpecError."""
    with pytest.raises(SpecError) as exc:
        AgentSpec(name="a", engine="langgraph", template="single", code="my.mod:build")
    msg = str(exc.value).lower()
    assert "template" in msg and "code" in msg


def test_template_alone_and_code_alone_are_fine():
    """Either template or code on its own is coherent — only together is an error."""
    assert AgentSpec(name="a", engine="langgraph", template="single").template == "single"
    assert AgentSpec(name="a", engine="langgraph", code="my.mod:build").code == "my.mod:build"


def test_coherence_error_is_spec_error_from_yaml(tmp_path):
    """An incoherent spec loaded from YAML surfaces the coherence failure as SpecError."""
    f = tmp_path / "a.yaml"
    f.write_text("name: a\nengine: echo\ntemplate: deepagents\n")
    with pytest.raises(SpecError):
        load_spec(f)


def test_code_hook_resolves_file_ref(tmp_path):
    """A ``code: path.py:function`` reference resolves to the real callable, which runs."""
    mod = tmp_path / "author.py"
    mod.write_text(
        textwrap.dedent(
            """
            from agentship.spec import AgentSpec

            def build():
                return AgentSpec(name="from-code", engine="echo")
            """
        )
    )
    fn = resolve_code(f"{mod}:build")
    spec = fn()
    # Assert the resolved callable actually produced the spec — not a stub.
    assert isinstance(spec, AgentSpec)
    assert spec.name == "from-code"


def test_code_hook_bad_ref_raises():
    """A malformed code reference (no ':function') fails loudly."""
    with pytest.raises(SpecError):
        resolve_code("no_colon_here")


def test_code_hook_missing_attr_raises(tmp_path):
    """A code ref naming a missing attribute raises SpecError mentioning it."""
    mod = tmp_path / "author.py"
    mod.write_text("x = 1\n")
    with pytest.raises(SpecError) as exc:
        resolve_code(f"{mod}:nonexistent")
    assert "nonexistent" in str(exc.value)
