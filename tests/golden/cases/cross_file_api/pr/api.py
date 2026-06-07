"""User API."""


def get_user(user_id: str) -> dict:
    return {"id": user_id, "name": "demo"}
