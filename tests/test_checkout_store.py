from __future__ import annotations

import asyncio
from typing import Any

from app.services.checkout_store import upsert_checkout_from_ucp


class FakeSupabase:
    def __init__(self, *, existing: dict[str, Any] | None = None) -> None:
        self.existing = existing
        self.update_payload: dict[str, Any] | None = None

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        if self.existing is not None:
            return [self.existing]
        return []

    async def update(self, table: str, payload: dict, *, query: dict[str, str]) -> Any:
        self.update_payload = dict(payload)
        return {}

    async def insert(self, table: str, payload: dict) -> Any:
        row = dict(payload)
        row["id"] = "chk-new"
        return [row]


def test_upsert_checkout_preserves_id_when_update_returns_empty() -> None:
    async def run() -> None:
        existing = {
            "id": "chk-existing",
            "profile_id": "prof-1",
            "business_id": "biz-1",
            "external_checkout_id": "merchant-chk-1",
            "total_minor": 1599,
            "currency": "USD",
        }
        fake = FakeSupabase(existing=existing)
        row = await upsert_checkout_from_ucp(
            fake,
            profile_id="prof-1",
            business_id="biz-1",
            checkout_payload={
                "id": "merchant-chk-1",
                "status": "ready_for_complete",
                "currency": "USD",
                "totals": [{"type": "total", "amount": 1599}],
            },
        )

        assert row["id"] == "chk-existing"
        assert row["status"] == "ready_for_complete"
        assert fake.update_payload is not None

    asyncio.run(run())
