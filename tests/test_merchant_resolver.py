from __future__ import annotations

import asyncio
from typing import Any

from app.services.merchant_resolver import merchant_from_business_row, resolve_merchant


class FakeSupabase:
    def __init__(self, *, business: dict[str, Any] | None = None) -> None:
        self.business = business or {
            "id": "biz-1",
            "ucp_base_url": "https://store.example.com/ucp/v1",
            "ucp_capabilities": {"dev.ucp.shopping.checkout": [{}]},
            "encrypted_ucp_api_key": "vendor-secret",
        }
        self.rpc_calls: list[tuple[str, dict | None]] = []

    async def rpc(self, name: str, payload: dict | None = None) -> Any:
        self.rpc_calls.append((name, payload))
        if name == "resolve_business_by_domain":
            return {
                "business_id": self.business["id"],
                "ucp_base_url": self.business.get("ucp_base_url"),
                "ucp_capabilities": self.business.get("ucp_capabilities"),
            }
        return {}

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        if table == "businesses":
            return [self.business]
        return []


def test_merchant_from_business_row_includes_inbound_api_key() -> None:
    merchant = merchant_from_business_row(
        {
            "id": "biz-1",
            "ucp_base_url": "https://store.example.com/ucp/v1",
            "ucp_capabilities": {"dev.ucp.shopping.checkout": [{}]},
            "encrypted_ucp_api_key": "vendor-secret",
        }
    )
    assert merchant.inbound_api_key == "vendor-secret"
    assert merchant.ucp_base_url == "https://store.example.com/ucp/v1"


def test_resolve_merchant_loads_inbound_api_key_from_business() -> None:
    async def run() -> None:
        fake = FakeSupabase()
        merchant = await resolve_merchant(fake, "https://store.example.com")
        assert merchant.business_id == "biz-1"
        assert merchant.inbound_api_key == "vendor-secret"
        assert fake.rpc_calls[0][0] == "resolve_business_by_domain"

    asyncio.run(run())
