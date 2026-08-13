"""C6: the bounded-retry *decision* — pure functions the supervisor graph wires into an edge.

Retries are a conditional-edge loop with a **hard cap**, never ``while True``. The decision is pure:
given the latest dispatch results, a per-specialist attempts counter, the cap, and the configured
retryable categories, decide which specialists to re-run and whether to loop or move on. Keeping it
pure means it is trivially testable and the cap provably terminates the loop (no livelock, even
across a resume).
"""

from __future__ import annotations

from agentship.primitives.conflict_resolver import SpecialistResult
from agentship.primitives.retry import (
    classify_error,
    is_retryable,
    should_retry,
    specialists_to_retry,
)


def _result(name: str, error: str | None) -> SpecialistResult:
    return SpecialistResult(name=name, output={}, confidence=None, error=error)


class TestClassifyError:
    def test_no_error_is_no_category(self) -> None:
        """A successful result has no retry category."""
        assert classify_error(None) is None

    def test_timeout_message_is_the_timeout_category(self) -> None:
        """The timeout error the dispatcher produces classifies as 'timeout'."""
        assert classify_error("timed out after 5s") == "timeout"

    def test_any_other_error_is_specialist_error(self) -> None:
        """A non-timeout failure classifies as the generic 'specialist_error'."""
        assert classify_error("boom") == "specialist_error"


class TestIsRetryable:
    def test_timeout_retryable_only_when_configured(self) -> None:
        """A timeout is retryable iff 'timeout' is in the configured categories."""
        r = _result("a", "timed out after 1s")
        assert is_retryable(r, ["timeout"]) is True
        assert is_retryable(r, ["specialist_error"]) is False

    def test_success_is_never_retryable(self) -> None:
        """A specialist that succeeded is never retried."""
        assert is_retryable(_result("a", None), ["timeout", "specialist_error"]) is False


class TestSpecialistsToRetry:
    def test_only_retryable_under_the_cap(self) -> None:
        """Return names that are retryable AND still under the attempt cap."""
        results = [_result("a", "boom"), _result("b", "boom"), _result("c", None)]
        attempts = {"a": 1, "b": 3}  # b already at cap
        names = specialists_to_retry(results, attempts, 3, ["specialist_error"])
        assert names == ["a"]  # b exhausted, c succeeded

    def test_empty_when_all_exhausted_guarantees_termination(self) -> None:
        """At the cap, nothing retries → the loop terminates (reaches merge)."""
        results = [_result("a", "boom")]
        attempts = {"a": 3}
        assert specialists_to_retry(results, attempts, 3, ["specialist_error"]) == []


class TestShouldRetry:
    def test_retry_while_something_is_retryable_under_cap(self) -> None:
        """should_retry is True while any specialist can still be retried."""
        results = [_result("a", "boom")]
        assert should_retry(results, {"a": 0}, 3, ["specialist_error"]) is True

    def test_merge_once_exhausted(self) -> None:
        """should_retry is False at the cap — the graph moves on to merge (no livelock)."""
        results = [_result("a", "boom")]
        assert should_retry(results, {"a": 3}, 3, ["specialist_error"]) is False
