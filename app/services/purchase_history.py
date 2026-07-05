from __future__ import annotations

from typing import Any

from app.db.supabase import SupabaseClient


def _first_rows(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    if isinstance(result, dict):
        return [result]
    return []


def _coerce_business(row: dict[str, Any]) -> dict[str, Any]:
    embedded = row.get("businesses")
    if isinstance(embedded, list) and embedded:
        embedded = embedded[0]
    if not isinstance(embedded, dict):
        embedded = {}
    return {
        "business_id": str(row.get("business_id") or embedded.get("id") or ""),
        "name": embedded.get("name"),
        "category": embedded.get("category"),
    }


def _public_order(row: dict[str, Any]) -> dict[str, Any]:
    merchant = _coerce_business(row)
    return {
        "order_id": row.get("external_order_id"),
        "status": row.get("status"),
        "total_minor": int(row.get("total_minor") or 0),
        "currency": row.get("currency") or "USD",
        "permalink_url": row.get("permalink_url"),
        "created_at": row.get("created_at"),
        "merchant": merchant,
    }


async def get_purchase_history(
    supabase: SupabaseClient,
    *,
    profile_id: str,
    business_id: str | None = None,
    status: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)

    select_query: dict[str, str] = {
        "profile_id": f"eq.{profile_id}",
        "select": "id,external_order_id,status,total_minor,currency,permalink_url,created_at,business_id,businesses(name,category)",
        "order": "created_at.desc",
        "limit": str(safe_limit + 1),
        "offset": str(safe_offset),
    }

    if business_id:
        select_query["business_id"] = f"eq.{business_id}"
    if status:
        select_query["status"] = f"eq.{status}"
    if created_from and created_to:
        select_query["and"] = f"(created_at.gte.{created_from},created_at.lte.{created_to})"
    elif created_from:
        select_query["created_at"] = f"gte.{created_from}"
    elif created_to:
        select_query["created_at"] = f"lte.{created_to}"

    rows = _first_rows(await supabase.select("orders", query=select_query))
    has_next_page = len(rows) > safe_limit
    page_rows = rows[:safe_limit]

    return {
        "orders": [_public_order(row) for row in page_rows],
        "pagination": {
            "limit": safe_limit,
            "offset": safe_offset,
            "has_next_page": has_next_page,
        },
    }
