"""``Skill`` — a how-to guidance bundle that teaches an agent how to use its tools/MCP (Phase 03).

A skill is **not** a tool. A :class:`~agentship.tools.tool.Tool` is an executable capability (the
hands); a ``Skill`` is a playbook — instructions like "to triage GitHub issues, list open issues,
then label by area, then …" that tell the model *when* and *how* to use the tools/MCP an agent
already has. AgentShip adopts the **Agent Skills open standard** (agentskills.io): a skill is a
folder with a ``SKILL.md`` (YAML frontmatter + Markdown body). This type is the parsed, validated
in-memory form; the loader lives in :mod:`agentship.skills.loader`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Skill(BaseModel):
    """One parsed SKILL.md — the portable Agent Skills schema (only name + description required).

    ``name`` (lowercase-hyphen, ``1..64``, must match the skill's directory) and ``description``
    (``1..1024`` — it states *what* the skill does *and when* to use it, and drives selection) are
    required. ``instructions`` is the Markdown body (the how-to). ``license``/``compatibility``/
    ``metadata`` are optional passthrough; ``allowed_tools`` (the spec's experimental
    ``allowed-tools``) is a parsed list of tool names the skill expects to use.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    instructions: str = ""
    license: str | None = None
    compatibility: str | None = None
    metadata: dict[str, str] = {}
    allowed_tools: list[str] = []
