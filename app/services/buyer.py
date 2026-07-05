from __future__ import annotations

from typing import Any

from app.auth.api_keys import ApiKeyContext
from app.config import Settings


def buyer_from_context(context: ApiKeyContext, settings: Settings) -> dict[str, Any]:
    raw = context.raw if isinstance(context.raw, dict) else {}
    profile_id = context.profile_id or "unknown"

    buyer: dict[str, Any] = {
        "email": raw.get("email") or f"buyer+{profile_id}@example.com",
        "phone_number": raw.get("phone_number") or raw.get("phone") or settings.demo_phone_number,
    }

    if raw.get("first_name"):
        buyer["first_name"] = raw["first_name"]
    if raw.get("last_name"):
        buyer["last_name"] = raw["last_name"]

    return buyer


def merge_buyer(context_buyer: dict[str, Any], agent_buyer: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(agent_buyer or {})
    for key in ("email", "phone_number", "first_name", "last_name"):
        if context_buyer.get(key):
            merged[key] = context_buyer[key]
    return merged
