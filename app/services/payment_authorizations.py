from __future__ import annotations

from typing import Any

from app.db.supabase import SupabaseClient

PRE_CAPTURE_STATUSES = frozenset({"created", "reserved", "submitted", "authorized"})
TERMINAL_ACCREDIT_FAILURE_STATUSES = frozenset({"released", "failed"})


class PaymentAuthorizationError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def to_authorization_status(payment_status: str | None) -> str:
    if not payment_status:
        return "unknown"
    if payment_status == "captured":
        return "completed"
    return payment_status


def _first_row(result: Any) -> dict[str, Any] | None:
    if isinstance(result, list):
        if not result:
            return None
        row = result[0]
        return row if isinstance(row, dict) else None
    if isinstance(result, dict):
        return result
    return None


class PaymentAuthorizationService:
    def __init__(self, supabase: SupabaseClient) -> None:
        self._supabase = supabase

    async def get_authorization(self, authorization_id: str, business_id: str) -> dict[str, Any]:
        payment, checkout = await self._load_owned_payment(authorization_id, business_id)
        return self._serialize_authorization(payment, checkout, business_id)

    async def accredit(
        self,
        authorization_id: str,
        business_id: str,
        *,
        order_id: str,
        amount_minor: int,
        currency: str,
    ) -> dict[str, Any]:
        payment, checkout = await self._load_owned_payment(authorization_id, business_id)
        payment_status = str(payment.get("status") or "")

        if payment_status == "captured":
            self._validate_amount_currency(payment, amount_minor=amount_minor, currency=currency)
            return self._completed_response(payment)

        if payment_status in TERMINAL_ACCREDIT_FAILURE_STATUSES:
            raise PaymentAuthorizationError(
                f"Payment authorization is '{payment_status}' and cannot be accredited.",
                status_code=409,
            )

        if payment_status not in PRE_CAPTURE_STATUSES:
            raise PaymentAuthorizationError(
                f"Payment authorization is '{payment_status or 'unknown'}' and cannot be accredited.",
                status_code=409,
            )

        self._validate_amount_currency(payment, amount_minor=amount_minor, currency=currency)

        local_order = await self._get_or_create_order(
            checkout=checkout,
            business_id=business_id,
            external_order_id=order_id,
            amount_minor=amount_minor,
            currency=currency,
        )

        capture_result = await self._supabase.rpc(
            "capture_checkout_payment",
            {
                "p_payment_id": authorization_id,
                "p_order_id": local_order["id"],
            },
        )

        transaction_id = self._extract_transaction_id(capture_result, payment, local_order)
        return {"status": "completed", "transaction_id": transaction_id}

    async def release(
        self,
        authorization_id: str,
        business_id: str,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        payment, _checkout = await self._load_owned_payment(authorization_id, business_id)
        payment_status = str(payment.get("status") or "")

        if payment_status == "captured":
            return {"status": to_authorization_status(payment_status)}

        if payment_status == "released":
            return {"status": to_authorization_status(payment_status)}

        if payment_status in {"reserved", "submitted"}:
            await self._supabase.rpc(
                "release_checkout_payment",
                {"p_payment_id": authorization_id},
            )
            return {"status": "released"}

        return {"status": to_authorization_status(payment_status or None)}

    async def _load_owned_payment(
        self,
        authorization_id: str,
        business_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payment = _first_row(
            await self._supabase.select(
                "payments",
                query={"id": f"eq.{authorization_id}", "select": "*"},
            )
        )
        if payment is None:
            raise PaymentAuthorizationError("Payment authorization not found.", status_code=404)

        checkout_session_id = payment.get("checkout_session_id")
        if not checkout_session_id:
            raise PaymentAuthorizationError("Payment authorization not found.", status_code=404)

        checkout = _first_row(
            await self._supabase.select(
                "checkout_sessions",
                query={"id": f"eq.{checkout_session_id}", "select": "*"},
            )
        )
        if checkout is None or str(checkout.get("business_id")) != business_id:
            raise PaymentAuthorizationError("Payment authorization not found.", status_code=404)

        return payment, checkout

    def _serialize_authorization(
        self,
        payment: dict[str, Any],
        checkout: dict[str, Any],
        business_id: str,
    ) -> dict[str, Any]:
        checkout_id = checkout.get("external_checkout_id") or checkout.get("id")
        return {
            "id": str(payment["id"]),
            "status": to_authorization_status(payment.get("status")),
            "amount_minor": payment.get("amount_minor"),
            "currency": payment.get("currency"),
            "checkout_id": str(checkout_id) if checkout_id is not None else None,
            "merchant_id": business_id,
        }

    def _validate_amount_currency(
        self,
        payment: dict[str, Any],
        *,
        amount_minor: int,
        currency: str,
    ) -> None:
        payment_amount = payment.get("amount_minor")
        if payment_amount is not None and int(payment_amount) != int(amount_minor):
            raise PaymentAuthorizationError(
                f"Amount mismatch: expected {payment_amount}, got {amount_minor}.",
                status_code=400,
            )

        payment_currency = payment.get("currency")
        if payment_currency and str(payment_currency).upper() != str(currency).upper():
            raise PaymentAuthorizationError(
                f"Currency mismatch: expected {payment_currency}, got {currency}.",
                status_code=400,
            )

    async def _get_or_create_order(
        self,
        *,
        checkout: dict[str, Any],
        business_id: str,
        external_order_id: str,
        amount_minor: int,
        currency: str,
    ) -> dict[str, Any]:
        checkout_session_id = checkout["id"]
        existing = _first_row(
            await self._supabase.select(
                "orders",
                query={
                    "checkout_session_id": f"eq.{checkout_session_id}",
                    "external_order_id": f"eq.{external_order_id}",
                    "select": "*",
                    "limit": "1",
                },
            )
        )
        if existing is not None:
            return existing

        created = await self._supabase.insert(
            "orders",
            {
                "checkout_session_id": checkout_session_id,
                "business_id": business_id,
                "profile_id": checkout.get("profile_id"),
                "external_order_id": external_order_id,
                "status": "created",
                "total_minor": amount_minor,
                "currency": currency,
                "snapshot": {
                    "external_order_id": external_order_id,
                    "source": "platform_accredit",
                },
            },
        )
        row = _first_row(created)
        if row is None:
            raise PaymentAuthorizationError("Failed to create order.", status_code=500)
        return row

    def _completed_response(self, payment: dict[str, Any]) -> dict[str, Any]:
        transaction_id = payment.get("order_id") or payment.get("id")
        return {
            "status": "completed",
            "transaction_id": str(transaction_id) if transaction_id is not None else None,
        }

    def _extract_transaction_id(
        self,
        capture_result: Any,
        payment: dict[str, Any],
        order: dict[str, Any],
    ) -> str | None:
        if isinstance(capture_result, dict):
            for key in ("transaction_id", "order_id", "payment_id", "id"):
                value = capture_result.get(key)
                if value is not None:
                    return str(value)

        order_id = order.get("id") or payment.get("order_id") or payment.get("id")
        return str(order_id) if order_id is not None else None
