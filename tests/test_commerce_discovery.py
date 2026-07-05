from __future__ import annotations

import asyncio
from typing import Any

from app.services.commerce_discovery import discover_commerces


class FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.last_query: dict[str, str] | None = None

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        self.last_query = query
        if table != "businesses":
            return []
        limit = int((query or {}).get("limit", "20"))
        offset = int((query or {}).get("offset", "0"))
        return self.rows[offset : offset + limit]


def test_discover_commerces_feed_maps_public_fields() -> None:
    async def run() -> None:
        fake = FakeSupabase(
            [
                {
                    "id": "biz-1",
                    "name": "Demo Store",
                    "category": "apparel",
                    "description": "Shirts and tees",
                    "well_known_url": "https://shop.example.com/.well-known/ucp",
                    "status": "active",
                    "created_at": "2026-07-04T12:00:00Z",
                }
            ]
        )

        payload = await discover_commerces(fake)

        assert payload["commerces"] == [
            {
                "business_id": "biz-1",
                "name": "Demo Store",
                "category": "apparel",
                "description": "Shirts and tees",
                "merchant_url": "https://shop.example.com",
                "status": "active",
            }
        ]
        assert payload["pagination"] == {"limit": 20, "offset": 0, "has_next_page": False}
        assert fake.last_query is not None
        assert fake.last_query["status"] == "eq.active"
        assert fake.last_query["order"] == "created_at.desc"

    asyncio.run(run())


def test_discover_commerces_with_query_adds_or_filter() -> None:
    async def run() -> None:
        fake = FakeSupabase([])

        await discover_commerces(fake, query="apparel", limit=5, offset=10)

        assert fake.last_query is not None
        assert fake.last_query["or"] == (
            "(name.ilike.*apparel*,description.ilike.*apparel*,category.ilike.*apparel*)"
        )
        assert fake.last_query["limit"] == "6"
        assert fake.last_query["offset"] == "10"

    asyncio.run(run())


def test_discover_commerces_has_next_page_when_extra_row_returned() -> None:
    async def run() -> None:
        fake = FakeSupabase(
            [
                {"id": "1", "name": "A", "category": "x", "description": "", "well_known_url": None, "status": "active"},
                {"id": "2", "name": "B", "category": "x", "description": "", "well_known_url": None, "status": "active"},
            ]
        )

        payload = await discover_commerces(fake, limit=1)

        assert len(payload["commerces"]) == 1
        assert payload["pagination"]["has_next_page"] is True

    asyncio.run(run())
