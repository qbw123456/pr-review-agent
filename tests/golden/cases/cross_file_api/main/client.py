"""API client."""

from api import get_user


def fetch_demo_user() -> dict:
    return get_user(1)
