from __future__ import annotations

from typing import Any

from app.db.supabase import SupabaseClient


async def record_usage_event(supabase: SupabaseClient, **fields: Any) -> None:
    try:
        payload = {"is_purchase": False, "revenue_minor": 0, **fields}
        await supabase.insert("usage_events", payload)
    except Exception:
        return
