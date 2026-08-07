"""Tests for the authoring layer: YAML loading, extra-key rejection, and ``code:``."""

from __future__ import annotations

import textwrap

import pytest
from agentship.errors import SpecError
from agentship.spec import AgentSpec, load_spec, resolve_code


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
