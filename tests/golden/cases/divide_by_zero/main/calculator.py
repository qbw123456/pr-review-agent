"""Simple numeric helpers."""


def average(items: list[float]) -> float:
    """Return arithmetic mean of items."""
    if not items:
        raise ValueError("items cannot be empty")
    return sum(items) / len(items)
