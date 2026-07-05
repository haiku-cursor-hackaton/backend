from __future__ import annotations

from typing import Any

from app.db.supabase import SupabaseClient

_WELL_KNOWN_SUFFIX = "/.well-known/ucp"


def _first_rows(result: Any) -> list[dict[str, Any]]:
    if isinstance(result, list):
        return [row for row in result if isinstance(row, dict)]
    if isinstance(result, dict):
        return [result]
    return []


def _merchant_url_from_well_known(well_known_url: str | None) -> str | None:
    if not well_known_url:
        return None
    text = str(well_known_url).rstrip("/")
    if text.endswith(_WELL_KNOWN_SUFFIX):
        return text[: -len(_WELL_KNOWN_SUFFIX)]
    return text


def _public_commerce(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "business_id": str(row["id"]),
        "name": row.get("name"),
        "category": row.get("category"),
        "description": row.get("description") or "",
        "merchant_url": _merchant_url_from_well_known(row.get("well_known_url")),
        "status": row.get("status"),
    }


async def discover_commerces(
    supabase: SupabaseClient,
    *,
    query: str | None = None,
    categories: list[str] | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    safe_limit = max(1, min(limit, 100))
    safe_offset = max(0, offset)

    select_query: dict[str, str] = {
        "status": "eq.active",
        "select": "id,name,category,description,well_known_url,status,created_at",
        "order": "created_at.desc",
        "limit": str(safe_limit + 1),
        "offset": str(safe_offset),
    }

    if query:
        escaped = query.replace(",", "\\,")
        select_query["or"] = (
            f"(name.ilike.*{escaped}*,description.ilike.*{escaped}*,category.ilike.*{escaped}*)"
        )

    if categories:
        cleaned = [category.strip() for category in categories if category and category.strip()]
        if cleaned:
            select_query["category"] = f"in.({','.join(cleaned)})"

    rows = _first_rows(await supabase.select("businesses", query=select_query))
    has_next_page = len(rows) > safe_limit
    page_rows = rows[:safe_limit]

    return {
        "commerces": [_public_commerce(row) for row in page_rows],
        "pagination": {
            "limit": safe_limit,
            "offset": safe_offset,
            "has_next_page": has_next_page,
        },
    }
