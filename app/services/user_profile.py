from __future__ import annotations

from typing import Any

from app.auth.api_keys import ApiKeyContext
from app.db.supabase import SupabaseClient


def _first_row(result: Any) -> dict[str, Any] | None:
    if isinstance(result, list):
        if not result:
            return None
        row = result[0]
        return row if isinstance(row, dict) else None
    if isinstance(result, dict):
        return result
    return None


async def get_user_profile(
    supabase: SupabaseClient,
    context: ApiKeyContext,
    *,
    currency: str = "USD",
) -> dict[str, Any]:
    profile_id = context.profile_id
    if not profile_id:
        raise ValueError("User profile is only available for client MCP API keys")

    raw = context.raw if isinstance(context.raw, dict) else {}

    profile_row = _first_row(
        await supabase.select(
            "profiles",
            query={
                "id": f"eq.{profile_id}",
                "select": "full_name,account_type",
                "limit": "1",
            },
        )
    )

    wallet_row = _first_row(
        await supabase.select(
            "wallets",
            query={
                "profile_id": f"eq.{profile_id}",
                "currency": f"eq.{currency}",
                "select": "available_minor,reserved_minor,currency",
                "limit": "1",
            },
        )
    )

    full_name = (profile_row or {}).get("full_name") or raw.get("full_name")
    email = raw.get("email")

    wallet_currency = str((wallet_row or {}).get("currency") or currency)
    available_minor = int((wallet_row or {}).get("available_minor") or 0)
    reserved_minor = int((wallet_row or {}).get("reserved_minor") or 0)

    return {
        "full_name": full_name,
        "email": email,
        "wallet": {
            "currency": wallet_currency,
            "available_minor": available_minor,
            "reserved_minor": reserved_minor,
        },
    }
