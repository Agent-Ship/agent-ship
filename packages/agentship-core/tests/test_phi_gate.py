"""The PHI privacy gate: hashed user id + content never on a span by default (§4.6).

Covers the identity rule (:func:`hashed_user_id`) and re-asserts the content rule from the caller's
side, so the two halves of the gate are pinned in one place. The SaaS-exporter half of the gate is
enforced in the observability factory and covered by its own factory test.
"""

from __future__ import annotations

from agentship.observability import hashed_user_id, replay_attributes, semconv
from agentship.observability.phi import HASH_SALT_ENV


def test_hash_is_deterministic_for_a_caller() -> None:
    """The same (user_id, salt) always hashes to the same value so a caller correlates."""
    assert hashed_user_id("alice", salt="s") == hashed_user_id("alice", salt="s")


def test_hash_hides_the_raw_id_and_is_short() -> None:
    """The raw id is not recoverable from the digest, which is a short hex string."""
    digest = hashed_user_id("alice", salt="s")
    assert "alice" not in digest
    assert len(digest) == 16 and all(c in "0123456789abcdef" for c in digest)


def test_salt_changes_the_hash() -> None:
    """A different salt yields a different digest (rainbow-table resistance)."""
    assert hashed_user_id("alice", salt="s1") != hashed_user_id("alice", salt="s2")


def test_salt_defaults_to_env(monkeypatch) -> None:
    """With no explicit salt, the env salt is used."""
    monkeypatch.setenv(HASH_SALT_ENV, "envsalt")
    assert hashed_user_id("alice") == hashed_user_id("alice", salt="envsalt")


def test_content_gate_off_emits_no_message_bodies() -> None:
    """capture_content=false records the request hash but never the message content."""
    attrs = replay_attributes(
        {"model": "m", "messages": [{"role": "user", "content": "phi"}]},
        None,
        capture_content=False,
    )
    assert semconv.AS_REPLAY_REQUEST_HASH in attrs
    assert "phi" not in repr(attrs)
