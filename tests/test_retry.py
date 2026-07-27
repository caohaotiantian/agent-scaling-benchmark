import pytest

from aibench.retry import is_retryable_error, retry_call


def test_retry_succeeds_after_failures():
    n = {"i": 0}

    def flaky():
        n["i"] += 1
        if n["i"] < 3:
            raise ConnectionError("connection reset by peer")
        return "ok"

    assert retry_call(flaky, max_attempts=4, base_backoff_s=0.01, max_backoff_s=0.02) == "ok"
    assert n["i"] == 3


def test_non_retryable_raises_immediately():
    n = {"i": 0}

    def bad():
        n["i"] += 1
        raise ValueError("logic error")

    with pytest.raises(ValueError):
        retry_call(bad, max_attempts=5, base_backoff_s=0.01)
    assert n["i"] == 1


def test_is_retryable():
    assert is_retryable_error(TimeoutError("timeout"))
    assert is_retryable_error(RuntimeError("429 rate limit"))
    assert not is_retryable_error(ValueError("bad schema"))
