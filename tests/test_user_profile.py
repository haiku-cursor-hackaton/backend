from __future__ import annotations

import asyncio
from typing import Any

from app.auth.api_keys import ApiKeyContext
from app.services.user_profile import get_user_profile


class FakeSupabase:
    def __init__(self) -> None:
        self.select_results: dict[str, Any] = {}

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        key = table
        if query:
            key = f"{table}:{query.get('select', '')}"
        return self.select_results.get(key, self.select_results.get(table, []))


def test_get_user_profile_returns_non_sensitive_fields() -> None:
    async def run() -> None:
        fake = FakeSupabase()
        fake.select_results["profiles:full_name,account_type"] = [
            {"full_name": "[DEMO] Test Client", "account_type": "client"}
        ]
        fake.select_results["wallets:available_minor,reserved_minor,currency"] = [
            {"available_minor": 10000, "reserved_minor": 0, "currency": "USD"}
        ]

        context = ApiKeyContext(
            profile_id="profile-1",
            scopes=["wallet:read"],
            raw={"email": "demo-client@genko.local"},
        )

        payload = await get_user_profile(fake, context)

        assert payload == {
            "full_name": "[DEMO] Test Client",
            "email": "demo-client@genko.local",
            "wallet": {
                "currency": "USD",
                "available_minor": 10000,
                "reserved_minor": 0,
            },
        }

    asyncio.run(run())


def test_get_user_profile_requires_client_profile_id() -> None:
    async def run() -> None:
        fake = FakeSupabase()
        context = ApiKeyContext(business_id="biz-1", scopes=["wallet:read"])

        try:
            await get_user_profile(fake, context)
        except ValueError as exc:
            assert "client MCP API keys" in str(exc)
        else:
            raise AssertionError("Expected ValueError")

    asyncio.run(run())
