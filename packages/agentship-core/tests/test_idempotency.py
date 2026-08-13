"""C8.1: the deterministic tool-idempotency key + its canonical-JSON arg serialization.

`idem_key` is the ONE formula shared with P09 (design §C8 / §13.6):
``sha256(thread_id | node_id | tool_name | canonical_json(args))``. These tests pin the two
properties every downstream layer (P02 checkpoint mirror, P09 durable ledger) relies on: the
key is **stable across argument key-order permutations**, and it is **deterministic** — same
inputs, byte-identical key, every time.
"""

from __future__ import annotations

import hashlib

from agentship.primitives.idempotency import canonical_json, idem_key


class TestCanonicalJson:
    """Canonical JSON: deterministic, whitespace-free, key-order-independent."""

    def test_sorts_object_keys(self) -> None:
        """Two dicts differing only in key insertion order serialize identically."""
        assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})

    def test_is_compact_and_sorted(self) -> None:
        """No incidental whitespace; keys emitted in sorted order."""
        assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'

    def test_sorts_nested_objects(self) -> None:
        """Key-order independence holds recursively, not just at the top level."""
        left = canonical_json({"x": {"q": 1, "p": 2}, "y": [1, 2]})
        right = canonical_json({"y": [1, 2], "x": {"p": 2, "q": 1}})
        assert left == right

    def test_preserves_list_order(self) -> None:
        """List order is semantic and must NOT be sorted away."""
        assert canonical_json([3, 1, 2]) != canonical_json([1, 2, 3])


class TestIdemKey:
    """The composite key: format, stability, and field separation."""

    def test_is_a_sha256_hex_digest(self) -> None:
        """The key is a 64-char lowercase hex sha256 digest."""
        key = idem_key("t1", "n1", "search", {"q": "hi"})
        assert len(key) == 64 and key == key.lower()
        int(key, 16)  # parses as hex or raises

    def test_stable_across_arg_key_order(self) -> None:
        """The whole point: permuting arg keys yields the same key."""
        a = idem_key("t1", "n1", "search", {"q": "hi", "limit": 5})
        b = idem_key("t1", "n1", "search", {"limit": 5, "q": "hi"})
        assert a == b

    def test_deterministic_over_many_runs(self) -> None:
        """Same inputs → byte-identical key every time (guards identical-resume)."""
        keys = {idem_key("t", "n", "tool", {"a": 1, "b": [1, {"z": 2}]}) for _ in range(100)}
        assert len(keys) == 1

    def test_each_field_changes_the_key(self) -> None:
        """thread_id, node_id, tool_name, and args are each part of the key."""
        base = idem_key("t1", "n1", "search", {"q": "hi"})
        assert idem_key("t2", "n1", "search", {"q": "hi"}) != base
        assert idem_key("t1", "n2", "search", {"q": "hi"}) != base
        assert idem_key("t1", "n1", "lookup", {"q": "hi"}) != base
        assert idem_key("t1", "n1", "search", {"q": "bye"}) != base

    def test_matches_the_documented_formula(self) -> None:
        """The key equals sha256 over the exact shared byte layout (P09 depends on this)."""
        expected_payload = "\x1f".join(["t1", "n1", "search", canonical_json({"q": "hi"})])
        expected = hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()
        assert idem_key("t1", "n1", "search", {"q": "hi"}) == expected

    def test_no_field_boundary_collision(self) -> None:
        """A separator between fields prevents "ab|c" and "a|bc" style collisions."""
        assert idem_key("t", "n", "ab", {}) != idem_key("t", "n", "a", {})
