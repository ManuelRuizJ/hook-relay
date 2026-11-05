import random

BASE_DELAY_SECONDS = 2.0
MAX_DELAY_SECONDS = 300.0
JITTER_FRACTION = 0.1


def compute_backoff(
    attempts: int, *, rng: random.Random | random.SystemRandom | None = None
) -> float:
    """Exponential backoff with jitter for the given 1-indexed attempt number.

    Returns seconds to wait before the next retry:
        attempt 1 -> ~2s, attempt 2 -> ~4s, attempt 3 -> ~8s, ... capped at 5min.
    Jitter (0-10% of the raw delay) prevents synchronized retries across
    many events that failed at the same moment (thundering herd).
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    raw = min(BASE_DELAY_SECONDS * (2 ** (attempts - 1)), MAX_DELAY_SECONDS)
    r = rng or random
    return raw + r.uniform(0, raw * JITTER_FRACTION)