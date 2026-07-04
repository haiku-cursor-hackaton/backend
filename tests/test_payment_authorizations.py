from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.payment_authorizations import (
    PaymentAuthorizationError,
    PaymentAuthorizationService,
    to_authorization_status,
)

PAYMENT_ID = "pay-11111111-1111-1111-1111-111111111111"
CHECKOUT_ID = "chk-22222222-2222-2222-2222-222222222222"
ORDER_ID = "ord-33333333-3333-3333-3333-333333333333"
BUSINESS_ID = "biz-44444444-4444-4444-4444-444444444444"
OTHER_BUSINESS_ID = "biz-55555555-5555-5555-5555-555555555555"
PROFILE_ID = "prof-66666666-6666-6666-6666-666666666666"


class FakeSupabase:
    def __init__(
        self,
        *,
        payments: list[dict[str, Any]] | None = None,
        checkouts: list[dict[str, Any]] | None = None,
        orders: list[dict[str, Any]] | None = None,
    ) -> None:
        self.payments = {str(row["id"]): dict(row) for row in (payments or [])}
        self.checkouts = {str(row["id"]): dict(row) for row in (checkouts or [])}
        self.orders = [dict(row) for row in (orders or [])]
        self.rpc_calls: list[tuple[str, dict | None]] = []
        self.insert_calls: list[tuple[str, dict]] = []

    async def rpc(self, name: str, payload: dict | None = None) -> Any:
        self.rpc_calls.append((name, payload))
        if name == "capture_checkout_payment":
            payment_id = str((payload or {}).get("p_payment_id"))
            order_id = str((payload or {}).get("p_order_id"))
            payment = self.payments.get(payment_id)
            if payment is not None:
                payment["status"] = "captured"
                payment["order_id"] = order_id
            return {"payment_id": payment_id, "order_id": order_id}
        if name == "release_checkout_payment":
            payment_id = str((payload or {}).get("p_payment_id"))
            payment = self.payments.get(payment_id)
            if payment is not None:
                payment["status"] = "released"
            return {"payment_id": payment_id, "status": "released"}
        return {}

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        query = query or {}
        if table == "payments":
            payment_id = _eq_value(query.get("id"))
            payment = self.payments.get(payment_id)
            return [payment] if payment is not None else []
        if table == "checkout_sessions":
            checkout_id = _eq_value(query.get("id"))
            checkout = self.checkouts.get(checkout_id)
            return [checkout] if checkout is not None else []
        if table == "orders":
            checkout_session_id = _eq_value(query.get("checkout_session_id"))
            external_order_id = _eq_value(query.get("external_order_id"))
            matches = [
                row
                for row in self.orders
                if str(row.get("checkout_session_id")) == checkout_session_id
                and str(row.get("external_order_id")) == external_order_id
            ]
            return matches[:1]
        return []

    async def insert(self, table: str, payload: dict) -> Any:
        self.insert_calls.append((table, dict(payload)))
        if table != "orders":
            return [payload]
        row = dict(payload)
        row["id"] = ORDER_ID
        self.orders.append(row)
        return [row]


def _eq_value(raw: str | None) -> str:
    assert raw is not None
    assert raw.startswith("eq.")
    return raw[3:]


def _sample_payment(*, status: str = "submitted") -> dict[str, Any]:
    return {
        "id": PAYMENT_ID,
        "checkout_session_id": CHECKOUT_ID,
        "amount_minor": 1500,
        "currency": "USD",
        "status": status,
    }


def _sample_checkout(*, business_id: str = BUSINESS_ID) -> dict[str, Any]:
    return {
        "id": CHECKOUT_ID,
        "business_id": business_id,
        "profile_id": PROFILE_ID,
        "external_checkout_id": "merchant-checkout-42",
        "status": "active",
        "total_minor": 1500,
        "currency": "USD",
    }


def test_to_authorization_status_maps_captured_to_completed() -> None:
    assert to_authorization_status("captured") == "completed"
    assert to_authorization_status("reserved") == "reserved"
    assert to_authorization_status(None) == "unknown"


def test_get_authorization_enforces_ownership_and_returns_external_checkout_id() -> None:
    async def run() -> None:
        supabase = FakeSupabase(
            payments=[_sample_payment(status="reserved")],
            checkouts=[_sample_checkout()],
        )
        service = PaymentAuthorizationService(supabase)

        result = await service.get_authorization(PAYMENT_ID, BUSINESS_ID)

        assert result == {
            "id": PAYMENT_ID,
            "status": "reserved",
            "amount_minor": 1500,
            "currency": "USD",
            "checkout_id": "merchant-checkout-42",
            "merchant_id": BUSINESS_ID,
        }

        with pytest.raises(PaymentAuthorizationError) as exc_info:
            await service.get_authorization(PAYMENT_ID, OTHER_BUSINESS_ID)

        assert exc_info.value.status_code == 404

    asyncio.run(run())


def test_accredit_creates_order_and_calls_capture_with_local_order_id() -> None:
    async def run() -> None:
        supabase = FakeSupabase(
            payments=[_sample_payment(status="submitted")],
            checkouts=[_sample_checkout()],
        )
        service = PaymentAuthorizationService(supabase)

        result = await service.accredit(
            PAYMENT_ID,
            BUSINESS_ID,
            order_id="merchant-order-99",
            amount_minor=1500,
            currency="USD",
        )

        assert result["status"] == "completed"
        assert result["transaction_id"] == ORDER_ID
        assert supabase.insert_calls == [
            (
                "orders",
                {
                    "checkout_session_id": CHECKOUT_ID,
                    "business_id": BUSINESS_ID,
                    "profile_id": PROFILE_ID,
                    "external_order_id": "merchant-order-99",
                    "status": "created",
                    "total_minor": 1500,
                    "currency": "USD",
                    "snapshot": {
                        "external_order_id": "merchant-order-99",
                        "source": "platform_accredit",
                    },
                },
            )
        ]
        assert supabase.rpc_calls == [
            (
                "capture_checkout_payment",
                {"p_payment_id": PAYMENT_ID, "p_order_id": ORDER_ID},
            )
        ]

    asyncio.run(run())


def test_accredit_is_idempotent_when_payment_already_captured() -> None:
    async def run() -> None:
        payment = _sample_payment(status="captured")
        payment["order_id"] = "existing-order-id"
        supabase = FakeSupabase(
            payments=[payment],
            checkouts=[_sample_checkout()],
        )
        service = PaymentAuthorizationService(supabase)

        result = await service.accredit(
            PAYMENT_ID,
            BUSINESS_ID,
            order_id="merchant-order-99",
            amount_minor=1500,
            currency="USD",
        )

        assert result == {
            "status": "completed",
            "transaction_id": "existing-order-id",
        }
        assert supabase.rpc_calls == []
        assert supabase.insert_calls == []

    asyncio.run(run())


def test_release_calls_rpc_for_submitted_and_is_idempotent_for_released() -> None:
    async def run() -> None:
        submitted_supabase = FakeSupabase(
            payments=[_sample_payment(status="submitted")],
            checkouts=[_sample_checkout()],
        )
        submitted_service = PaymentAuthorizationService(submitted_supabase)

        submitted_result = await submitted_service.release(PAYMENT_ID, BUSINESS_ID, reason="order_failed")

        assert submitted_result == {"status": "released"}
        assert submitted_supabase.rpc_calls == [
            ("release_checkout_payment", {"p_payment_id": PAYMENT_ID})
        ]

        released_supabase = FakeSupabase(
            payments=[_sample_payment(status="released")],
            checkouts=[_sample_checkout()],
        )
        released_service = PaymentAuthorizationService(released_supabase)

        released_result = await released_service.release(PAYMENT_ID, BUSINESS_ID)

        assert released_result == {"status": "released"}
        assert released_supabase.rpc_calls == []

    asyncio.run(run())
