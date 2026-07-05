from __future__ import annotations

import asyncio
from typing import Any

from app.services.merchant_balance import get_merchant_balance


class FakeSupabase:
    def __init__(self) -> None:
        self.select_results: dict[str, list[dict[str, Any]]] = {}

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        key = f"{table}:{query}"
        return self.select_results.get(key, [])


def test_get_merchant_balance_returns_wallet_and_transactions() -> None:
    supabase = FakeSupabase()
    supabase.select_results["businesses:{'owner_id': 'eq.owner-1', 'select': 'id,name', 'order': 'created_at.asc'}"] = [
        {"id": "biz-1", "name": "Demo Store"}
    ]
    supabase.select_results[
        "wallets:{'business_id': 'eq.biz-1', 'currency': 'eq.USD', 'select': 'id,available_minor,reserved_minor,currency', 'limit': '1'}"
    ] = [
        {
            "id": "wallet-1",
            "available_minor": 3198,
            "reserved_minor": 0,
            "currency": "USD",
        }
    ]
    supabase.select_results[
        "merchant_transactions:{'business_id': 'eq.biz-1', 'select': 'id,payment_id,order_id,amount_minor,currency,type,created_at', 'order': 'created_at.desc', 'limit': '50'}"
    ] = [
        {
            "id": "tx-1",
            "payment_id": "pay-1",
            "order_id": "ord-1",
            "amount_minor": 1599,
            "currency": "USD",
            "type": "credit",
            "created_at": "2026-07-05T01:00:00Z",
        },
        {
            "id": "tx-2",
            "payment_id": "pay-2",
            "order_id": "ord-2",
            "amount_minor": 1599,
            "currency": "USD",
            "type": "credit",
            "created_at": "2026-07-05T00:00:00Z",
        },
    ]

    result = asyncio.run(get_merchant_balance(supabase, "owner-1"))

    assert result["business_id"] == "biz-1"
    assert result["business_name"] == "Demo Store"
    assert result["available_minor"] == 3198
    assert result["reserved_minor"] == 0
    assert len(result["transactions"]) == 2
    assert result["transactions"][0]["payment_id"] == "pay-1"


def test_get_merchant_balance_without_business_returns_zeros() -> None:
    supabase = FakeSupabase()

    result = asyncio.run(get_merchant_balance(supabase, "owner-without-business"))

    assert result["business_id"] is None
    assert result["available_minor"] == 0
    assert result["transactions"] == []
