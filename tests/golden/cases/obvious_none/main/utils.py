"""String helpers."""


def greet(name: str) -> str:
    """Return a greeting for the given name."""
    if name is None:
        raise ValueError("name cannot be None")
    return f"Hello, {name.upper()}"
