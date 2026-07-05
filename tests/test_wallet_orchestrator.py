from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.config import Settings
from app.services.merchant_resolver import ResolvedMerchant
from app.services.wallet_orchestrator import (
    CompleteCheckoutOrchestrator,
    build_insufficient_balance_error,
)


PROFILE_ID = "prof-11111111-1111-1111-1111-111111111111"
BUSINESS_ID = "biz-22222222-2222-2222-2222-222222222222"
CHECKOUT_ROW_ID = "chk-local-33333333-3333-3333-3333-333333333333"
EXTERNAL_CHECKOUT_ID = "merchant-chk-42"
PAYMENT_ID = "pay-44444444-4444-4444-4444-444444444444"


class FakeUcpClient:
    def __init__(self, *, checkout: dict[str, Any], complete_response: dict[str, Any] | None = None) -> None:
        self.checkout = dict(checkout)
        self.complete_response = complete_response
        self.complete_payload: dict[str, Any] | None = None
        self.closed = False

    async def get_checkout(self, checkout_id: str) -> dict[str, Any]:
        assert checkout_id == EXTERNAL_CHECKOUT_ID
        return dict(self.checkout)

    async def complete_checkout(
        self,
        checkout_id: str,
        payload: dict[str, Any] | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        self.complete_payload = payload
        assert self.complete_response is not None
        return dict(self.complete_response)

    async def close(self) -> None:
        self.closed = True


class FakeSupabase:
    def __init__(
        self,
        *,
        wallet_available: int = 10_000,
        checkout_row: dict[str, Any] | None = None,
    ) -> None:
        self.wallet_available = wallet_available
        self.checkout_row = checkout_row or {
            "id": CHECKOUT_ROW_ID,
            "profile_id": PROFILE_ID,
            "business_id": BUSINESS_ID,
            "external_checkout_id": EXTERNAL_CHECKOUT_ID,
            "total_minor": 1500,
            "currency": "USD",
        }
        self.rpc_calls: list[tuple[str, dict | None]] = []
        self.update_calls: list[tuple[str, dict, dict]] = []
        self.insert_calls: list[tuple[str, dict]] = []

    async def rpc(self, name: str, payload: dict | None = None) -> Any:
        self.rpc_calls.append((name, payload))
        if name == "reserve_checkout_payment":
            return {"payment_id": PAYMENT_ID}
        if name == "mark_payment_submitted":
            return {"payment_id": payload.get("p_payment_id") if payload else None}
        if name == "release_checkout_payment":
            return {"payment_id": payload.get("p_payment_id") if payload else None}
        return {}

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        query = query or {}
        if table == "wallets":
            return [
                {
                    "profile_id": PROFILE_ID,
                    "currency": "USD",
                    "available_minor": self.wallet_available,
                }
            ]
        if table == "checkout_sessions":
            return [self.checkout_row]
        if table == "orders":
            return []
        return []

    async def update(self, table: str, payload: dict, *, query: dict[str, str]) -> Any:
        self.update_calls.append((table, payload, query))
        if table == "checkout_sessions":
            self.checkout_row = {**self.checkout_row, **payload}
            return [self.checkout_row]
        return [payload]

    async def insert(self, table: str, payload: dict) -> Any:
        self.insert_calls.append((table, dict(payload)))
        row = dict(payload)
        if "id" not in row:
            row["id"] = CHECKOUT_ROW_ID
        if table == "checkout_sessions":
            self.checkout_row = row
        return [row]


class ReserveRaisesFakeSupabase(FakeSupabase):
    async def rpc(self, name: str, payload: dict | None = None) -> Any:
        self.rpc_calls.append((name, payload))
        if name == "reserve_checkout_payment":
            request = httpx.Request("POST", "https://example.supabase.co/rest/v1/rpc/reserve_checkout_payment")
            response = httpx.Response(
                400,
                request=request,
                json={"message": "insufficient_platform_balance"},
            )
            raise httpx.HTTPStatusError("bad request", request=request, response=response)
        if name == "mark_payment_submitted":
            return {"payment_id": payload.get("p_payment_id") if payload else None}
        if name == "release_checkout_payment":
            return {"payment_id": payload.get("p_payment_id") if payload else None}
        return {}

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        if table == "payments":
            return [
                {
                    "id": PAYMENT_ID,
                    "checkout_session_id": CHECKOUT_ROW_ID,
                    "status": "reserved",
                }
            ]
        return await super().select(table, query=query)


def _merchant() -> ResolvedMerchant:
    return ResolvedMerchant(
        business_id=BUSINESS_ID,
        ucp_base_url="https://merchant.example",
        ucp_capabilities={"dev.ucp.shopping.checkout": [{}]},
        raw={"business_id": BUSINESS_ID},
    )


def _settings() -> Settings:
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-key",
    )


def test_build_insufficient_balance_error_shape() -> None:
    error = build_insufficient_balance_error(1500, "USD")
    assert error["ucp"]["status"] == "error"
    assert error["messages"][0]["code"] == "insufficient_platform_balance"
    assert error["messages"][0]["severity"] == "recoverable"


def test_complete_passthrough_when_not_ready() -> None:
    async def run() -> tuple[dict[str, Any], FakeSupabase]:
        supabase = FakeSupabase()
        ucp = FakeUcpClient(checkout={"id": EXTERNAL_CHECKOUT_ID, "status": "draft", "currency": "USD"})
        orchestrator = CompleteCheckoutOrchestrator(supabase, _settings())
        payload = await orchestrator.complete(
            ucp_client=ucp,
            merchant=_merchant(),
            profile_id=PROFILE_ID,
            external_checkout_id=EXTERNAL_CHECKOUT_ID,
        )
        return payload, supabase

    payload, supabase = asyncio.run(run())
    assert payload["status"] == "draft"
    assert supabase.rpc_calls == []


def test_complete_returns_insufficient_balance_without_reserve() -> None:
    async def run() -> tuple[dict[str, Any], FakeSupabase]:
        supabase = FakeSupabase(wallet_available=100)
        ucp = FakeUcpClient(
            checkout={
                "id": EXTERNAL_CHECKOUT_ID,
                "status": "ready_for_complete",
                "currency": "USD",
                "totals": [{"type": "total", "amount": 1500}],
            }
        )
        orchestrator = CompleteCheckoutOrchestrator(supabase, _settings())
        payload = await orchestrator.complete(
            ucp_client=ucp,
            merchant=_merchant(),
            profile_id=PROFILE_ID,
            external_checkout_id=EXTERNAL_CHECKOUT_ID,
        )
        return payload, supabase

    payload, supabase = asyncio.run(run())
    assert payload["messages"][0]["code"] == "insufficient_platform_balance"
    assert supabase.rpc_calls == []


def test_complete_reserves_submits_and_passes_payment_reference() -> None:
    async def run() -> tuple[dict[str, Any], FakeSupabase, FakeUcpClient]:
        supabase = FakeSupabase()
        ucp = FakeUcpClient(
            checkout={
                "id": EXTERNAL_CHECKOUT_ID,
                "status": "ready_for_complete",
                "currency": "USD",
                "totals": [{"type": "total", "amount": 1500}],
            },
            complete_response={
                "id": EXTERNAL_CHECKOUT_ID,
                "status": "completed",
                "order": {"id": "order-99", "status": "created"},
            },
        )
        orchestrator = CompleteCheckoutOrchestrator(supabase, _settings())
        payload = await orchestrator.complete(
            ucp_client=ucp,
            merchant=_merchant(),
            profile_id=PROFILE_ID,
            external_checkout_id=EXTERNAL_CHECKOUT_ID,
        )
        return payload, supabase, ucp

    payload, supabase, ucp = asyncio.run(run())

    assert payload["status"] == "completed"
    assert supabase.rpc_calls[0][0] == "reserve_checkout_payment"
    assert supabase.rpc_calls[0][1]["p_checkout_session_id"] == CHECKOUT_ROW_ID
    assert supabase.rpc_calls[1] == ("mark_payment_submitted", {"p_payment_id": PAYMENT_ID})

    instrument = ucp.complete_payload["payment"]["instruments"][0]
    assert instrument["credential"]["reference"] == PAYMENT_ID
    assert instrument["handler_id"] == "offline"

    order_inserts = [call for call in supabase.insert_calls if call[0] == "orders"]
    assert len(order_inserts) == 1


def test_complete_uses_reserved_payment_when_rpc_returns_http_400_but_row_exists() -> None:
    async def run() -> tuple[dict[str, Any], ReserveRaisesFakeSupabase, FakeUcpClient]:
        supabase = ReserveRaisesFakeSupabase()
        ucp = FakeUcpClient(
            checkout={
                "id": EXTERNAL_CHECKOUT_ID,
                "status": "ready_for_complete",
                "currency": "USD",
                "totals": [{"type": "total", "amount": 1500}],
            },
            complete_response={
                "id": EXTERNAL_CHECKOUT_ID,
                "status": "completed",
                "order": {"id": "order-99", "status": "created"},
            },
        )
        orchestrator = CompleteCheckoutOrchestrator(supabase, _settings())
        payload = await orchestrator.complete(
            ucp_client=ucp,
            merchant=_merchant(),
            profile_id=PROFILE_ID,
            external_checkout_id=EXTERNAL_CHECKOUT_ID,
        )
        return payload, supabase, ucp

    payload, supabase, ucp = asyncio.run(run())

    assert payload["status"] == "completed"
    assert supabase.rpc_calls[0][0] == "reserve_checkout_payment"
    assert supabase.rpc_calls[1] == ("mark_payment_submitted", {"p_payment_id": PAYMENT_ID})
    instrument = ucp.complete_payload["payment"]["instruments"][0]
    assert instrument["credential"]["reference"] == PAYMENT_ID


def test_complete_accepts_string_payment_id_from_reserve_rpc() -> None:
    class StringReserveSupabase(FakeSupabase):
        async def rpc(self, name: str, payload: dict | None = None) -> Any:
            self.rpc_calls.append((name, payload))
            if name == "reserve_checkout_payment":
                return PAYMENT_ID
            if name == "mark_payment_submitted":
                return {"payment_id": payload.get("p_payment_id") if payload else None}
            return {}

    async def run() -> tuple[dict[str, Any], StringReserveSupabase, FakeUcpClient]:
        supabase = StringReserveSupabase()
        ucp = FakeUcpClient(
            checkout={
                "id": EXTERNAL_CHECKOUT_ID,
                "status": "ready_for_complete",
                "currency": "USD",
                "totals": [{"type": "total", "amount": 1500}],
            },
            complete_response={
                "id": EXTERNAL_CHECKOUT_ID,
                "status": "completed",
                "order": {"id": "order-99", "status": "created"},
            },
        )
        orchestrator = CompleteCheckoutOrchestrator(supabase, _settings())
        payload = await orchestrator.complete(
            ucp_client=ucp,
            merchant=_merchant(),
            profile_id=PROFILE_ID,
            external_checkout_id=EXTERNAL_CHECKOUT_ID,
        )
        return payload, supabase, ucp

    payload, supabase, ucp = asyncio.run(run())

    assert payload["status"] == "completed"
    assert supabase.rpc_calls[1] == ("mark_payment_submitted", {"p_payment_id": PAYMENT_ID})
    instrument = ucp.complete_payload["payment"]["instruments"][0]
    assert instrument["credential"]["reference"] == PAYMENT_ID


def test_complete_loads_reserved_payment_when_reserve_rpc_returns_empty_payload() -> None:
    class EmptyReserveSupabase(FakeSupabase):
        async def rpc(self, name: str, payload: dict | None = None) -> Any:
            self.rpc_calls.append((name, payload))
            if name == "reserve_checkout_payment":
                return {}
            if name == "mark_payment_submitted":
                return {"payment_id": payload.get("p_payment_id") if payload else None}
            return {}

        async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
            if table == "payments":
                return [{"id": PAYMENT_ID, "status": "reserved"}]
            return await super().select(table, query=query)

    async def run() -> tuple[dict[str, Any], EmptyReserveSupabase, FakeUcpClient]:
        supabase = EmptyReserveSupabase()
        ucp = FakeUcpClient(
            checkout={
                "id": EXTERNAL_CHECKOUT_ID,
                "status": "ready_for_complete",
                "currency": "USD",
                "totals": [{"type": "total", "amount": 1500}],
            },
            complete_response={
                "id": EXTERNAL_CHECKOUT_ID,
                "status": "completed",
                "order": {"id": "order-99", "status": "created"},
            },
        )
        orchestrator = CompleteCheckoutOrchestrator(supabase, _settings())
        payload = await orchestrator.complete(
            ucp_client=ucp,
            merchant=_merchant(),
            profile_id=PROFILE_ID,
            external_checkout_id=EXTERNAL_CHECKOUT_ID,
        )
        return payload, supabase, ucp

    payload, supabase, ucp = asyncio.run(run())

    assert payload["status"] == "completed"
    assert supabase.rpc_calls[1] == ("mark_payment_submitted", {"p_payment_id": PAYMENT_ID})
    instrument = ucp.complete_payload["payment"]["instruments"][0]
    assert instrument["credential"]["reference"] == PAYMENT_ID
