from __future__ import annotations

import asyncio
from typing import Any

from app.services.purchase_history import get_purchase_history


class FakeSupabase:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.last_query: dict[str, str] | None = None

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        self.last_query = query
        if table != "orders":
            return []
        limit = int((query or {}).get("limit", "20"))
        offset = int((query or {}).get("offset", "0"))
        return self.rows[offset : offset + limit]


def test_get_purchase_history_maps_orders() -> None:
    async def run() -> None:
        fake = FakeSupabase(
            [
                {
                    "id": "local-1",
                    "business_id": "biz-1",
                    "external_order_id": "ord-100",
                    "status": "completed",
                    "total_minor": 1599,
                    "currency": "USD",
                    "permalink_url": "https://shop.example.com/orders/ord-100",
                    "created_at": "2026-07-04T10:00:00Z",
                    "businesses": {"name": "Demo Store", "category": "apparel"},
                }
            ]
        )

        payload = await get_purchase_history(fake, profile_id="profile-1")

        assert payload["orders"] == [
            {
                "order_id": "ord-100",
                "status": "completed",
                "total_minor": 1599,
                "currency": "USD",
                "permalink_url": "https://shop.example.com/orders/ord-100",
                "created_at": "2026-07-04T10:00:00Z",
                "merchant": {
                    "business_id": "biz-1",
                    "name": "Demo Store",
                    "category": "apparel",
                },
            }
        ]
        assert fake.last_query is not None
        assert fake.last_query["profile_id"] == "eq.profile-1"
        assert fake.last_query["order"] == "created_at.desc"

    asyncio.run(run())


def test_get_purchase_history_applies_optional_filters() -> None:
    async def run() -> None:
        fake = FakeSupabase([])

        await get_purchase_history(
            fake,
            profile_id="profile-1",
            business_id="biz-1",
            status="completed",
            created_from="2026-07-01T00:00:00Z",
            created_to="2026-07-31T23:59:59Z",
            limit=10,
            offset=5,
        )

        assert fake.last_query is not None
        assert fake.last_query["business_id"] == "eq.biz-1"
        assert fake.last_query["status"] == "eq.completed"
        assert fake.last_query["and"] == (
            "(created_at.gte.2026-07-01T00:00:00Z,created_at.lte.2026-07-31T23:59:59Z)"
        )
        assert fake.last_query["limit"] == "11"
        assert fake.last_query["offset"] == "5"

    asyncio.run(run())


def test_get_purchase_history_has_next_page_when_extra_row_returned() -> None:
    async def run() -> None:
        fake = FakeSupabase(
            [
                {
                    "id": "1",
                    "business_id": "biz-1",
                    "external_order_id": "ord-1",
                    "status": "completed",
                    "total_minor": 100,
                    "currency": "USD",
                    "permalink_url": None,
                    "created_at": "2026-07-04T10:00:00Z",
                    "businesses": {"name": "A", "category": "x"},
                },
                {
                    "id": "2",
                    "business_id": "biz-1",
                    "external_order_id": "ord-2",
                    "status": "completed",
                    "total_minor": 200,
                    "currency": "USD",
                    "permalink_url": None,
                    "created_at": "2026-07-03T10:00:00Z",
                    "businesses": {"name": "A", "category": "x"},
                },
            ]
        )

        payload = await get_purchase_history(fake, profile_id="profile-1", limit=1)

        assert len(payload["orders"]) == 1
        assert payload["pagination"]["has_next_page"] is True

    asyncio.run(run())
