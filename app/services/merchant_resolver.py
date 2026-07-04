from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db.supabase import SupabaseClient
from app.services.merchant_registration import domain_from_url


class MerchantResolutionError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class CapabilityError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class ResolvedMerchant:
    business_id: str
    ucp_base_url: str
    ucp_capabilities: dict[str, Any]
    raw: dict[str, Any]


def _first_row(result: Any) -> dict[str, Any] | None:
    if isinstance(result, list):
        if not result:
            return None
        row = result[0]
        return row if isinstance(row, dict) else None
    if isinstance(result, dict):
        return result
    return None


def _coerce_capabilities(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


async def resolve_merchant(supabase: SupabaseClient, merchant_url: str) -> ResolvedMerchant:
    domain = domain_from_url(merchant_url)
    rpc_result = await supabase.rpc("resolve_business_by_domain", {"p_domain": domain})
    row = _first_row(rpc_result)
    if row is None:
        raise MerchantResolutionError(f"No registered merchant for domain: {domain}")

    business_id = row.get("business_id") or row.get("id")
    if not business_id:
        raise MerchantResolutionError(f"Merchant resolution missing business id for domain: {domain}")

    ucp_base_url = row.get("ucp_base_url")
    ucp_capabilities = _coerce_capabilities(row.get("ucp_capabilities"))

    if not ucp_base_url:
        business = _first_row(
            await supabase.select(
                "businesses",
                query={"id": f"eq.{business_id}", "select": "id,ucp_base_url,ucp_capabilities"},
            )
        )
        if business is None:
            raise MerchantResolutionError(f"Business not found: {business_id}")
        ucp_base_url = business.get("ucp_base_url")
        if not ucp_capabilities:
            ucp_capabilities = _coerce_capabilities(business.get("ucp_capabilities"))

    if not ucp_base_url:
        raise MerchantResolutionError(f"Merchant missing UCP base URL for domain: {domain}")

    return ResolvedMerchant(
        business_id=str(business_id),
        ucp_base_url=str(ucp_base_url).rstrip("/"),
        ucp_capabilities=ucp_capabilities,
        raw=row,
    )


def ensure_capability(merchant: ResolvedMerchant, capability: str) -> None:
    if capability not in merchant.ucp_capabilities:
        raise CapabilityError(f"Merchant does not support capability: {capability}")
