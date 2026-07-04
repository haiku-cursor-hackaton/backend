from __future__ import annotations

from typing import Any

from app.auth.api_keys import GeneratedAPIKey, KeyType, generate_api_key
from app.db.supabase import SupabaseClient


async def issue_api_key(
    supabase: SupabaseClient,
    key_type: KeyType,
    *,
    profile_id: str | None = None,
    business_id: str | None = None,
    scopes: list[str] | None = None,
    label: str | None = None,
) -> GeneratedAPIKey:
    generated = generate_api_key(key_type)
    payload: dict[str, Any] = {
        "key_type": key_type,
        "profile_id": profile_id,
        "business_id": business_id,
        "key_hash": generated.key_hash,
        "key_prefix": generated.key_prefix,
        "scopes": scopes or [],
        "status": "active",
        "label": label,
    }
    await supabase.insert("api_keys", payload)
    return generated
