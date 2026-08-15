"""Phase 03 · Skills — a ``Skill`` is a how-to guidance bundle (the Agent Skills open standard).

A skill teaches the agent *how* to use its tools/MCP — distinct from the tools themselves. We adopt
the ``agentskills.io`` SKILL.md format faithfully: a folder with a ``SKILL.md`` (YAML frontmatter +
Markdown body), the portable 6-field schema (only ``name`` + ``description`` required), ``name``
matching the directory, and unknown frontmatter keys rejected. These tests pin the parser/validator.
"""

from __future__ import annotations

import textwrap

import pytest
from agentship.errors import SpecError
from agentship.skills import Skill, load_skill, resolve_skill


def _write_skill(tmp_path, dirname: str, body: str) -> str:
    """Write a ``<dirname>/SKILL.md`` under tmp_path and return the skill folder path."""
    d = tmp_path / dirname
    d.mkdir()
    (d / "SKILL.md").write_text(textwrap.dedent(body).lstrip())
    return str(d)


def test_load_skill_parses_frontmatter_and_body(tmp_path):
    """A SKILL.md folder loads into a Skill with name/description/instructions/metadata."""
    path = _write_skill(
        tmp_path,
        "pdf-processing",
        """
        ---
        name: pdf-processing
        description: Extract text from PDFs. Use when the user mentions PDFs or forms.
        metadata:
          version: "1.0"
        ---
        # PDF Processing
        1. Use pdfplumber to extract text.
        """,
    )
    skill = load_skill(path)
    assert isinstance(skill, Skill)
    assert skill.name == "pdf-processing"
    assert "Use when the user mentions PDFs" in skill.description
    assert "pdfplumber" in skill.instructions
    assert skill.metadata == {"version": "1.0"}


def test_skill_name_must_match_its_directory(tmp_path):
    """Per the spec, ``name`` must equal the parent directory name."""
    path = _write_skill(
        tmp_path, "foo", "---\nname: bar\ndescription: does x. use for x.\n---\nbody"
    )
    with pytest.raises(SpecError, match="directory"):
        load_skill(path)


def test_skill_rejects_unknown_frontmatter_key(tmp_path):
    """Only the portable 6-field schema is allowed; an unknown key is a hard error."""
    path = _write_skill(
        tmp_path, "foo", "---\nname: foo\ndescription: does x. use for x.\nbogus: 1\n---\nbody"
    )
    with pytest.raises(SpecError, match="bogus"):
        load_skill(path)


def test_skill_requires_a_description(tmp_path):
    """``description`` is required (it drives selection)."""
    path = _write_skill(tmp_path, "foo", "---\nname: foo\n---\nbody")
    with pytest.raises(SpecError, match="description"):
        load_skill(path)


def test_allowed_tools_parses_space_separated(tmp_path):
    """The experimental ``allowed-tools`` field is a space-separated string → list."""
    path = _write_skill(
        tmp_path,
        "foo",
        "---\nname: foo\ndescription: x. use x.\nallowed-tools: calculator http_request\n---\nb",
    )
    skill = load_skill(path)
    assert skill.allowed_tools == ["calculator", "http_request"]


def test_resolve_skill_by_path(tmp_path):
    """``resolve_skill`` accepts a path to a skill folder and returns the loaded Skill."""
    path = _write_skill(tmp_path, "foo", "---\nname: foo\ndescription: does x. use for x.\n---\nb")
    assert resolve_skill(path).name == "foo"


def test_resolve_unknown_skill_is_a_spec_error():
    """An unresolvable skill reference fails fast with an actionable error."""
    with pytest.raises(SpecError, match="nope-not-a-skill"):
        resolve_skill("nope-not-a-skill")
