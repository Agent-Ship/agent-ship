"""Offline proof of the coordinator driver's routing rule (``normalize_label``).

The end-to-end coordinator run is live (the model classifies the request) and is exercised by hand
via ``python demos/coordinated_research.py`` — see MANUAL_TESTING.md §11. But the *rule* that turns
the coordinator's free-text reply into a ``quick``/``deep`` route is pure and worth pinning offline:
it must find ``deep`` robustly (despite punctuation/case/extra words) and otherwise default to the
cheap ``quick`` path.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# The demo ships scripts, not an installed package; put the repo root on the path so ``demos``
# imports (pytest only adds ``tests/`` by default).
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from demos.coordinated_research import normalize_label  # noqa: E402


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("deep", "deep"),
        ("DEEP", "deep"),
        ("deep.", "deep"),
        ("This needs deep research.", "deep"),
        ("quick", "quick"),
        ("Quick!", "quick"),
        ("just a fast lookup", "quick"),
        ("", "quick"),  # empty/unsure defaults to the cheap path
    ],
)
def test_normalize_label(reply, expected):
    """The label parser routes 'deep' anywhere in the reply, otherwise defaults to 'quick'."""
    assert normalize_label(reply) == expected
