"""Skills: how-to guidance bundles that teach an agent how to use its tools/MCP (Phase 03).

AgentShip adopts the **Agent Skills open standard** (agentskills.io): a skill is a folder with a
``SKILL.md`` (YAML frontmatter + Markdown instructions). Public surface: :class:`Skill` (the parsed
form), :func:`load_skill` (parse+validate a folder), the :data:`SKILLS` registry, and
:func:`resolve_skill` (turn a ``skills:`` reference into a loaded ``Skill``). A skill is distinct
from a :class:`~agentship.tools.Tool` — tools are the hands, skills are the playbook.
"""

from __future__ import annotations

from .loader import load_skill
from .registry import SKILLS, resolve_skill
from .render import render_agent_prompt
from .skill import Skill

__all__ = ["SKILLS", "Skill", "load_skill", "render_agent_prompt", "resolve_skill"]
