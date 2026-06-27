"""Load __skills__/*.md files into a single system-prompt block."""

from pathlib import Path
from typing import Optional


def load_skills(agent_dir: Path) -> Optional[str]:
    """Return concatenated markdown content from agent's __skills__/ dir, or None."""
    skills_dir = agent_dir / "__skills__"
    if not skills_dir.exists():
        return None

    texts = []
    for md_file in sorted(skills_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8").strip()
        if content:
            texts.append(content)

    if not texts:
        return None

    joined = "\n\n---\n\n".join(texts)
    return f"# Domain Knowledge\n\n{joined}"
