from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import httpx

from app.config import Settings
from app.db.supabase import SupabaseClient
from app.services.checkout_store import upsert_checkout_from_ucp, upsert_order_from_ucp
from app.services.merchant_resolver import ResolvedMerchant
from app.services.ucp_client import UcpRestClient


def _first_row(result: Any) -> dict[str, Any] | None:
    if isinstance(result, list):
        if not result:
            return None
        row = result[0]
        return row if isinstance(row, dict) else None
    if isinstance(result, dict):
        return result
    return None


def _extract_payment_id(result: Any) -> str | None:
    if isinstance(result, str) and result:
        return result
    row = _first_row(result)
    if row is None:
        return None
    for key in ("payment_id", "id"):
        value = row.get(key)
        if value is not None:
            return str(value)
    return None


def _extract_http_error_message(error: httpx.HTTPStatusError) -> str:
    try:
        payload = error.response.json()
    except Exception:
        return error.response.text or str(error)
    if isinstance(payload, dict):
        for key in ("message", "detail", "code"):
            value = payload.get(key)
            if value:
                return str(value)
    return str(payload)


async def _load_reserved_payment_id(
    supabase: SupabaseClient,
    *,
    checkout_row_id: str,
    idem_hash: str,
    attempts: int = 3,
    delay_seconds: float = 0.05,
) -> str | None:
    for attempt in range(attempts):
        payment_row = _first_row(
            await supabase.select(
                "payments",
                query={
                    "checkout_session_id": f"eq.{checkout_row_id}",
                    "idempotency_key_hash": f"eq.{idem_hash}",
                    "select": "*",
                    "limit": "1",
                },
            )
        )
        if payment_row and payment_row.get("id"):
            return str(payment_row["id"])
        if attempt + 1 < attempts:
            await asyncio.sleep(delay_seconds)
    return None


def build_insufficient_balance_error(amount_minor: int, currency: str) -> dict[str, Any]:
    return {
        "ucp": {"status": "error"},
        "messages": [
            {
                "code": "insufficient_platform_balance",
                "severity": "recoverable",
                "content": f"Insufficient platform balance for {amount_minor} {currency}.",
            }
        ],
    }


def build_reconciliation_required_error() -> dict[str, Any]:
    return {
        "ucp": {"status": "error"},
        "messages": [
            {
                "code": "reconciliation_required",
                "severity": "recoverable",
                "content": "Payment submitted but merchant completion failed; reconciliation required.",
            }
        ],
    }


def _idempotency_hash(profile_id: str, checkout_row_id: str, external_checkout_id: str) -> str:
    raw = f"{profile_id}:{checkout_row_id}:{external_checkout_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _offline_payment_payload(payment_id: str) -> dict[str, Any]:
    return {
        "payment": {
            "instruments": [
                {
                    "id": "offline",
                    "handler_id": "offline",
                    "type": "offline",
                    "selected": True,
                    "credential": {"reference": payment_id},
                }
            ]
        }
    }


def _is_completed_with_order(payload: dict[str, Any]) -> bool:
    status = payload.get("status")
    if status != "completed":
        ucp = payload.get("ucp")
        if isinstance(ucp, dict) and ucp.get("status") == "completed":
            status = "completed"
    return status == "completed" and payload.get("order") is not None


def _is_error_payload(payload: dict[str, Any]) -> bool:
    ucp = payload.get("ucp")
    if isinstance(ucp, dict) and ucp.get("status") == "error":
        return True
    status = payload.get("status")
    return status not in (None, "completed", "ready_for_complete")


class CompleteCheckoutOrchestrator:
    def __init__(self, supabase: SupabaseClient, settings: Settings) -> None:
        self._supabase = supabase
        self._settings = settings

    async def complete(
        self,
        *,
        ucp_client: UcpRestClient,
        merchant: ResolvedMerchant,
        profile_id: str,
        external_checkout_id: str,
    ) -> dict[str, Any]:
        checkout_payload = await ucp_client.get_checkout(external_checkout_id)
        checkout_row = await upsert_checkout_from_ucp(
            self._supabase,
            profile_id=profile_id,
            business_id=merchant.business_id,
            checkout_payload=checkout_payload,
        )

        status = checkout_payload.get("status")
        if status != "ready_for_complete":
            return checkout_payload

        total_minor = int(checkout_row.get("total_minor") or 0)
        currency = str(checkout_row.get("currency") or checkout_payload.get("currency") or "USD")

        wallet = _first_row(
            await self._supabase.select(
                "wallets",
                query={
                    "profile_id": f"eq.{profile_id}",
                    "currency": f"eq.{currency}",
                    "select": "*",
                    "limit": "1",
                },
            )
        )
        available = int((wallet or {}).get("available_minor") or 0)
        if available < total_minor:
            return build_insufficient_balance_error(total_minor, currency)

        checkout_row_id = checkout_row.get("id")
        if not checkout_row_id:
            return {
                "ucp": {"status": "error"},
                "messages": [
                    {
                        "code": "checkout_not_persisted",
                        "severity": "recoverable",
                        "content": "Checkout session could not be persisted locally.",
                    }
                ],
            }

        idem_hash = _idempotency_hash(profile_id, str(checkout_row_id), external_checkout_id)
        try:
            reserve_result = await self._supabase.rpc(
                "reserve_checkout_payment",
                {
                    "p_checkout_session_id": checkout_row_id,
                    "p_idempotency_key_hash": idem_hash,
                },
            )
            payment_id = await _load_reserved_payment_id(
                self._supabase,
                checkout_row_id=str(checkout_row_id),
                idem_hash=idem_hash,
            )
            if payment_id is None:
                payment_id = _extract_payment_id(reserve_result)
        except httpx.HTTPStatusError as exc:
            payment_id = await _load_reserved_payment_id(
                self._supabase,
                checkout_row_id=str(checkout_row_id),
                idem_hash=idem_hash,
            )
            if payment_id is None:
                message = _extract_http_error_message(exc)
                if "insufficient_platform_balance" in message:
                    return build_insufficient_balance_error(total_minor, currency)
                return {
                    "ucp": {"status": "error"},
                    "messages": [
                        {
                            "code": "payment_reserve_failed",
                            "severity": "recoverable",
                            "content": "Failed to reserve checkout payment.",
                        }
                    ],
                }
        if payment_id is None:
            return {
                "ucp": {"status": "error"},
                "messages": [
                    {
                        "code": "payment_reserve_failed",
                        "severity": "recoverable",
                        "content": "Failed to reserve checkout payment.",
                    }
                ],
            }

        await self._supabase.rpc("mark_payment_submitted", {"p_payment_id": payment_id})

        try:
            complete_payload = await ucp_client.complete_checkout(
                external_checkout_id,
                _offline_payment_payload(payment_id),
                ucp_agent=self._settings.gateway_agent_name,
            )
        except httpx.RequestError:
            await self._supabase.update(
                "payments",
                {"status": "reconciliation_required"},
                query={"id": f"eq.{payment_id}"},
            )
            return build_reconciliation_required_error()

        refreshed = await upsert_checkout_from_ucp(
            self._supabase,
            profile_id=profile_id,
            business_id=merchant.business_id,
            checkout_payload=complete_payload,
        )

        if _is_completed_with_order(complete_payload):
            order = complete_payload.get("order")
            if isinstance(order, dict):
                await upsert_order_from_ucp(
                    self._supabase,
                    checkout_row=refreshed,
                    business_id=merchant.business_id,
                    profile_id=profile_id,
                    order_payload=order,
                    checkout_payload=complete_payload,
                )
            return complete_payload

        if _is_error_payload(complete_payload):
            try:
                await self._supabase.rpc("release_checkout_payment", {"p_payment_id": payment_id})
            except Exception:
                pass

        return complete_payload
