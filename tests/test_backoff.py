import random

import pytest

from app.worker.backoff import (
    BASE_DELAY_SECONDS,
    MAX_DELAY_SECONDS,
    compute_backoff,
)


def test_compute_backoff_grows_exponentially():
    rng = random.Random(42)
    b1 = compute_backoff(1, rng=rng)
    b2 = compute_backoff(2, rng=rng)
    b3 = compute_backoff(3, rng=rng)

    assert BASE_DELAY_SECONDS <= b1 <= BASE_DELAY_SECONDS * 1.1
    assert 2 * BASE_DELAY_SECONDS <= b2 <= 2 * BASE_DELAY_SECONDS * 1.1
    assert 4 * BASE_DELAY_SECONDS <= b3 <= 4 * BASE_DELAY_SECONDS * 1.1


def test_compute_backoff_caps_at_max():
    rng = random.Random(0)
    b = compute_backoff(100, rng=rng)
    assert MAX_DELAY_SECONDS <= b <= MAX_DELAY_SECONDS * 1.1


def test_compute_backoff_rejects_zero():
    with pytest.raises(ValueError):
        compute_backoff(0)