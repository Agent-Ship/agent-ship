"""Parse + validate a ``SKILL.md`` folder into a :class:`~agentship.skills.skill.Skill`.

Faithful to the Agent Skills open standard (agentskills.io): split the leading ``---`` YAML
frontmatter from the Markdown body, allow only the portable 6 fields, require ``name`` +
``description``, enforce the ``name`` format and that it matches the parent directory, and reject
unknown keys with an actionable :class:`~agentship.errors.SpecError` (the spec's packagers do the
same). The experimental ``allowed-tools`` field (a space-separated string) is parsed into a list.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..errors import SpecError
from .skill import Skill

#: The portable Agent Skills frontmatter fields — anything else is rejected.
_ALLOWED_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
#: A valid skill name: lowercase letters/digits in hyphen-separated groups (no double/edge hyphen).
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split a SKILL.md into its YAML frontmatter mapping and its Markdown body."""
    if not text.startswith("---"):
        raise SpecError("SKILL.md must start with a `---` YAML frontmatter block")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise SpecError("SKILL.md frontmatter is not closed with a second `---`")
    try:
        front = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        raise SpecError(f"invalid YAML in SKILL.md frontmatter: {exc}") from exc
    if not isinstance(front, dict):
        raise SpecError("SKILL.md frontmatter must be a YAML mapping")
    return front, parts[2].strip()


def load_skill(path: str | Path) -> Skill:
    """Load and validate the ``SKILL.md`` in the folder ``path`` into a :class:`Skill`.

    Raises :class:`~agentship.errors.SpecError` on a missing file, malformed/unclosed frontmatter,
    an unknown frontmatter key, a missing ``name``/``description``, a bad ``name`` format, or a
    ``name`` that does not match the skill's directory.
    """
    folder = Path(path)
    md = folder / "SKILL.md"
    try:
        text = md.read_text()
    except FileNotFoundError as exc:
        raise SpecError(f"skill not found: no SKILL.md in {folder}") from exc

    front, body = _split_frontmatter(text)

    unknown = set(front) - _ALLOWED_KEYS
    if unknown:
        raise SpecError(
            f"SKILL.md in {folder.name!r} has unknown frontmatter key(s) {sorted(unknown)} — "
            f"the portable Agent Skills schema allows only {sorted(_ALLOWED_KEYS)}"
        )
    if not front.get("name"):
        raise SpecError(f"SKILL.md in {folder.name!r} is missing the required `name` field")
    if not front.get("description"):
        raise SpecError(f"skill {front['name']!r} is missing the required `description` field")

    name = str(front["name"])
    if not _NAME_RE.match(name) or len(name) > 64:
        raise SpecError(
            f"skill name {name!r} is invalid — use 1–64 lowercase letters/digits and hyphens "
            f"(no leading/trailing/double hyphen)"
        )
    if name != folder.name:
        raise SpecError(
            f"skill name {name!r} must match its directory name {folder.name!r} (Agent Skills spec)"
        )

    allowed_tools = str(front.get("allowed-tools", "")).split()
    return Skill(
        name=name,
        description=str(front["description"]),
        instructions=body,
        license=front.get("license"),
        compatibility=front.get("compatibility"),
        metadata=front.get("metadata") or {},
        allowed_tools=allowed_tools,
    )
