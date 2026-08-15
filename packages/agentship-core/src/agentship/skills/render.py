"""Compose an agent's system prompt from its base prompt + its declared skills' guidance (Phase 03).

For a **declared** ``skills:`` list (the author explicitly chose these for this agent), we inject
each skill's guidance into the system prompt: its ``name``/``description`` (so the model knows the
capability and when it applies) then its ``instructions`` (the how-to). This is the pragmatic
form of the Agent Skills "progressive disclosure" for author-declared skills; auto-selecting from a
large pool (surfacing only descriptions until a skill triggers) is a later enhancement.
"""

from __future__ import annotations

from .registry import resolve_skill

_HEADER = (
    "You have the following skills — how-to guidance for using your tools. "
    "Follow the relevant one when a request matches its description.\n"
)


def render_agent_prompt(prompt: str | None, skills: list[str] | None) -> str | None:
    """Return ``prompt`` augmented with the guidance of each skill in ``skills``.

    With no skills the base ``prompt`` is returned unchanged. Otherwise each reference is resolved
    (:func:`~agentship.skills.resolve_skill` — a name or a path to a SKILL.md folder) and rendered
    as a ``## Skill: <name>`` block (description + instructions), appended after the base prompt.
    """
    if not skills:
        return prompt

    blocks = []
    for ref in skills:
        skill = resolve_skill(ref)
        body = f"## Skill: {skill.name}\n{skill.description}\n\n{skill.instructions}".rstrip()
        blocks.append(body)
    guidance = _HEADER + "\n" + "\n\n".join(blocks)
    return f"{prompt}\n\n{guidance}" if prompt else guidance
