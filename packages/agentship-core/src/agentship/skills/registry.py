"""The skill registry + ``resolve_skill`` — how a ``skills:`` reference becomes a loaded ``Skill``.

A ``skills:`` entry is resolved by :func:`resolve_skill`: a **path to a skill folder** (containing
``SKILL.md``) is loaded directly; a bare **name** is looked up in :data:`SKILLS` (registered in code
or via the ``agentship.skills`` entry-point group, same mechanism as tools/engines). An unresolvable
reference fails fast with :class:`~agentship.errors.SpecError`.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import SpecError
from ..registry import Registry
from .loader import load_skill
from .skill import Skill

#: The registry of named skills, discoverable via the ``agentship.skills`` entry-point group.
SKILLS: Registry[Skill] = Registry("agentship.skills", label="skill")


def resolve_skill(ref: str) -> Skill:
    """Resolve a ``skills:`` reference to a :class:`Skill` — a path to a SKILL.md folder, or a name.

    If ``ref`` points at a directory containing ``SKILL.md`` it is loaded from disk; otherwise it is
    treated as a registered skill name looked up in :data:`SKILLS`. A reference that is neither
    raises :class:`~agentship.errors.SpecError`.
    """
    if (Path(ref) / "SKILL.md").is_file():
        return load_skill(ref)

    skill = SKILLS.get(ref)
    if skill is None:
        raise SpecError(
            f"no skill registered as {ref!r} — use a registered skill name, a path to a skill "
            f"folder containing SKILL.md, or install the package that provides it"
        )
    return skill
