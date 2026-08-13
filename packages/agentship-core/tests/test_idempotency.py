"""C8.1: the deterministic tool-idempotency key + its canonical-JSON arg serialization.

`idem_key` is the ONE formula shared with P09 (design §C8 / §13.6):
``sha256(thread_id | node_id | tool_name | canonical_json(args))``. These tests pin the two
properties every downstream layer (P02 checkpoint mirror, P09 durable ledger) relies on: the
key is **stable across argument key-order permutations**, and it is **deterministic** — same
inputs, byte-identical key, every time.
"""

from __future__ import annotations

import hashlib

import pytest
from agentship.primitives.idempotency import (
    DictLedger,
    LedgerEntry,
    call_once,
    canonical_json,
    idem_key,
)


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


class _Effect:
    """A fake side-effecting op: counts real fires vs. verify()s so tests can assert which ran."""

    def __init__(self, result: object = "did-it", verify_result: object = "verified") -> None:
        """Record the values the op returns when fired vs. when verified on resume."""
        self.calls = 0
        self.verify_calls = 0
        self._result = result
        self._verify_result = verify_result

    def __call__(self) -> object:
        """Perform the side effect (increments the fire counter)."""
        self.calls += 1
        return self._result

    def verify(self) -> object:
        """Check whether the effect already happened (resume path); never re-fires."""
        self.verify_calls += 1
        return self._verify_result


class TestCallOnce:
    """C8.2: the write-ahead intent ledger — exactly-once for non-idempotent writes on resume."""

    async def test_done_entry_replays_result_without_firing(self) -> None:
        """A recorded `done` key returns its result and never calls the effect (memoized replay)."""
        ledger = DictLedger({"k": LedgerEntry(status="done", result="recorded")})
        effect = _Effect()
        assert await call_once(ledger, "k", effect, idempotent=False) == "recorded"
        assert effect.calls == 0 and effect.verify_calls == 0

    async def test_pending_entry_verifies_not_refires(self) -> None:
        """A `pending` key (crash after write-ahead) triggers verify(), not a blind re-fire."""
        ledger = DictLedger({"k": LedgerEntry(status="pending")})
        effect = _Effect()
        result = await call_once(ledger, "k", effect, idempotent=False)
        assert effect.calls == 0 and effect.verify_calls == 1
        assert result == "verified"
        assert ledger.lookup("k").status == "done"  # verify resolves the pending intent

    async def test_fresh_idempotent_fires_once_then_memoizes(self) -> None:
        """An idempotent op fires once, records `done`, and a second call replays the memo."""
        ledger = DictLedger()
        effect = _Effect(result="r")
        assert await call_once(ledger, "k", effect, idempotent=True) == "r"
        assert await call_once(ledger, "k", effect, idempotent=True) == "r"
        assert effect.calls == 1  # second call short-circuits on the ledger

    async def test_idempotent_records_no_pending(self) -> None:
        """The idempotent path skips the write-ahead: it never leaves a `pending` marker."""
        ledger = DictLedger()
        await call_once(ledger, "k", _Effect(), idempotent=True)
        assert ledger.lookup("k").status == "done"

    async def test_write_ahead_records_pending_before_side_effect(self) -> None:
        """Non-idempotent: `pending` is durable BEFORE firing, so a crash mid-fire is recoverable.

        The effect raises, standing in for a crash between the side effect and the `done` record.
        The ledger must still hold `pending` afterward — proving the write-ahead happened first —
        so a later resume verifies instead of blindly re-firing.
        """
        ledger = DictLedger()

        class _Boom(_Effect):
            def __call__(self) -> object:
                self.calls += 1
                raise RuntimeError("crash mid-write")

        with pytest.raises(RuntimeError):
            await call_once(ledger, "k", _Boom(), idempotent=False)
        assert ledger.lookup("k").status == "pending"

    async def test_crash_between_write_and_ledger_then_resume_verifies(self) -> None:
        """End-to-end: a crashed non-idempotent write leaves `pending`; the resume verifies once."""
        ledger = DictLedger()

        class _Boom(_Effect):
            def __call__(self) -> object:
                self.calls += 1
                raise RuntimeError("crash")

        with pytest.raises(RuntimeError):
            await call_once(ledger, "k", _Boom(), idempotent=False)
        # resume: same key, a healthy effect — must verify, not re-run the side effect
        resumed = _Effect()
        result = await call_once(ledger, "k", resumed, idempotent=False)
        assert resumed.calls == 0 and resumed.verify_calls == 1
        assert result == "verified"

    async def test_supports_async_effects(self) -> None:
        """`call_once` awaits an async effect (tools are async) and records its result."""
        ledger = DictLedger()

        class _AsyncEffect:
            def __init__(self) -> None:
                self.calls = 0

            async def __call__(self) -> str:
                self.calls += 1
                return "async-result"

            async def verify(self) -> str:  # pragma: no cover - not hit in this test
                return "v"

        effect = _AsyncEffect()
        assert await call_once(ledger, "k", effect, idempotent=False) == "async-result"
        assert effect.calls == 1 and ledger.lookup("k").status == "done"
