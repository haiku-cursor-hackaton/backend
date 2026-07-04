from __future__ import annotations

from typing import Any

from app.db.supabase import SupabaseClient


def _first_row(result: Any) -> dict[str, Any] | None:
    if isinstance(result, list):
        if not result:
            return None
        row = result[0]
        return row if isinstance(row, dict) else None
    if isinstance(result, dict):
        return result
    return None


def extract_total_minor(checkout_payload: dict[str, Any]) -> tuple[int, str]:
    currency = str(checkout_payload.get("currency") or "USD")
    totals = checkout_payload.get("totals")
    if isinstance(totals, list):
        for item in totals:
            if isinstance(item, dict) and item.get("type") == "total":
                amount = item.get("amount") or item.get("amount_minor")
                if amount is not None:
                    return int(amount), currency
        subtotal = sum(
            int(entry.get("amount") or entry.get("amount_minor") or 0)
            for entry in totals
            if isinstance(entry, dict) and entry.get("type") == "subtotal"
        )
        if subtotal:
            return subtotal, currency

    amount = checkout_payload.get("total_minor") or checkout_payload.get("amount_minor")
    if amount is not None:
        return int(amount), currency
    return 0, currency


def _checkout_fields(
    *,
    profile_id: str,
    business_id: str,
    checkout_payload: dict[str, Any],
) -> dict[str, Any]:
    total_minor, currency = extract_total_minor(checkout_payload)
    external_checkout_id = str(checkout_payload.get("id") or checkout_payload.get("checkout_id") or "")
    return {
        "profile_id": profile_id,
        "business_id": business_id,
        "external_checkout_id": external_checkout_id,
        "status": checkout_payload.get("status"),
        "total_minor": total_minor,
        "currency": currency,
        "snapshot": checkout_payload,
        "expires_at": checkout_payload.get("expires_at"),
    }


async def upsert_checkout_from_ucp(
    supabase: SupabaseClient,
    *,
    profile_id: str,
    business_id: str,
    checkout_payload: dict[str, Any],
) -> dict[str, Any]:
    fields = _checkout_fields(
        profile_id=profile_id,
        business_id=business_id,
        checkout_payload=checkout_payload,
    )
    external_checkout_id = fields["external_checkout_id"]
    if not external_checkout_id:
        raise ValueError("Checkout payload missing id")

    existing = _first_row(
        await supabase.select(
            "checkout_sessions",
            query={
                "business_id": f"eq.{business_id}",
                "external_checkout_id": f"eq.{external_checkout_id}",
                "select": "*",
                "limit": "1",
            },
        )
    )
    if existing is not None:
        updated = await supabase.update(
            "checkout_sessions",
            {k: v for k, v in fields.items() if k not in {"profile_id", "business_id", "external_checkout_id"}},
            query={"id": f"eq.{existing['id']}"},
        )
        row = _first_row(updated)
        return row if row is not None else {**existing, **fields}

    inserted = await supabase.insert("checkout_sessions", fields)
    row = _first_row(inserted)
    if row is None:
        raise ValueError("Failed to persist checkout session")
    return row


async def find_checkout(
    supabase: SupabaseClient,
    *,
    profile_id: str,
    business_id: str | None = None,
    external_checkout_id: str,
) -> dict[str, Any] | None:
    query: dict[str, str] = {
        "profile_id": f"eq.{profile_id}",
        "external_checkout_id": f"eq.{external_checkout_id}",
        "select": "*",
        "limit": "1",
    }
    if business_id is not None:
        query["business_id"] = f"eq.{business_id}"
    return _first_row(await supabase.select("checkout_sessions", query=query))


async def upsert_order_from_ucp(
    supabase: SupabaseClient,
    *,
    checkout_row: dict[str, Any],
    business_id: str,
    profile_id: str,
    order_payload: dict[str, Any],
    checkout_payload: dict[str, Any],
) -> dict[str, Any]:
    external_order_id = str(order_payload.get("id") or order_payload.get("order_id") or "")
    if not external_order_id:
        raise ValueError("Order payload missing id")

    total_minor, currency = extract_total_minor(checkout_payload)
    if order_payload.get("total_minor") is not None:
        total_minor = int(order_payload["total_minor"])
    if order_payload.get("currency"):
        currency = str(order_payload["currency"])

    checkout_session_id = checkout_row["id"]
    existing = _first_row(
        await supabase.select(
            "orders",
            query={
                "checkout_session_id": f"eq.{checkout_session_id}",
                "external_order_id": f"eq.{external_order_id}",
                "select": "*",
                "limit": "1",
            },
        )
    )
    fields = {
        "checkout_session_id": checkout_session_id,
        "business_id": business_id,
        "profile_id": profile_id,
        "external_order_id": external_order_id,
        "status": order_payload.get("status") or "created",
        "total_minor": total_minor,
        "currency": currency,
        "snapshot": order_payload,
        "permalink_url": order_payload.get("permalink_url"),
    }
    if existing is not None:
        updated = await supabase.update(
            "orders",
            {k: v for k, v in fields.items() if k != "checkout_session_id"},
            query={"id": f"eq.{existing['id']}"},
        )
        row = _first_row(updated)
        return row if row is not None else {**existing, **fields}

    inserted = await supabase.insert("orders", fields)
    row = _first_row(inserted)
    if row is None:
        raise ValueError("Failed to persist order")
    return row
