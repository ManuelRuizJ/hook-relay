"""Read Prometheus samples in tests.

Metrics live in the process-wide default registry and keep counting
across tests, so assertions compare a value before and after an action.
"""

from prometheus_client import REGISTRY


def sample(name: str, **labels: str) -> float:
    """Current value of a sample, 0.0 if the series does not exist yet."""
    return REGISTRY.get_sample_value(name, labels) or 0.0
