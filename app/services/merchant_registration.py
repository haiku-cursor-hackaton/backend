from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from app.db.supabase import SupabaseClient
from app.services.key_issuer import issue_api_key


class MerchantRegistrationError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def normalize_root_url(root_url: str) -> str:
    return root_url.rstrip("/")


def domain_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raw_host = parsed.netloc or parsed.path.split("/")[0]
        host = raw_host.split(":", 1)[0]
    if not host:
        raise MerchantRegistrationError(f"Could not extract domain from URL: {url}")
    return host.lower()


def well_known_url(root_url: str) -> str:
    return f"{normalize_root_url(root_url)}/.well-known/ucp"


def extract_rest_endpoint(profile: dict[str, Any]) -> str:
    ucp = profile.get("ucp")
    if not isinstance(ucp, dict):
        raise MerchantRegistrationError("UCP profile missing 'ucp' object")

    services = ucp.get("services", {}).get("dev.ucp.shopping", [])
    if not isinstance(services, list):
        raise MerchantRegistrationError("UCP profile missing dev.ucp.shopping services")

    for service in services:
        if isinstance(service, dict) and service.get("transport") == "rest":
            endpoint = service.get("endpoint")
            if endpoint:
                return str(endpoint).rstrip("/")

    raise MerchantRegistrationError("UCP profile has no REST shopping service endpoint")


def extract_capabilities(profile: dict[str, Any]) -> dict[str, Any]:
    ucp = profile.get("ucp")
    if not isinstance(ucp, dict):
        return {}
    capabilities = ucp.get("capabilities")
    return dict(capabilities) if isinstance(capabilities, dict) else {}


def _first_row(result: Any) -> dict[str, Any] | None:
    if isinstance(result, list):
        if not result:
            return None
        row = result[0]
        return row if isinstance(row, dict) else None
    if isinstance(result, dict):
        return result
    return None


def _require_row_id(row: dict[str, Any], entity: str) -> str:
    row_id = row.get("id")
    if not row_id:
        raise MerchantRegistrationError(f"{entity} insert did not return an id")
    return str(row_id)


class MerchantRegistrationService:
    def __init__(self, supabase: SupabaseClient) -> None:
        self._supabase = supabase

    async def bootstrap_pending(
        self,
        *,
        owner_id: str,
        name: str,
        category: str | None,
        description: str | None = None,
    ) -> dict[str, Any]:
        business_result = await self._supabase.insert(
            "businesses",
            {
                "owner_id": owner_id,
                "name": name,
                "category": category,
                "description": description,
                "status": "pending",
                "well_known_url": None,
                "ucp_capabilities": {},
            },
        )
        business_row = _first_row(business_result)
        if business_row is None:
            raise MerchantRegistrationError("Business insert did not return a row")
        business_id = _require_row_id(business_row, "Business")

        sdk_key = await issue_api_key(
            self._supabase,
            "sdk",
            business_id=business_id,
            scopes=["payment:verify", "payment:accredit", "payment:release"],
            label=f"{name} SDK key",
        )

        return {
            "business_id": business_id,
            "status": "pending",
            "sdk_api_key": sdk_key.plaintext,
            "sdk_api_key_prefix": sdk_key.key_prefix,
        }

    async def link_url(
        self,
        *,
        owner_id: str,
        business_id: str,
        root_url: str,
        ucp_inbound_api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        business_row = _first_row(
            await self._supabase.select(
                "businesses",
                query={
                    "id": f"eq.{business_id}",
                    "select": "id,owner_id",
                    "limit": "1",
                },
            )
        )
        if business_row is None:
            raise MerchantRegistrationError("Business not found")
        if str(business_row.get("owner_id")) != owner_id:
            raise MerchantRegistrationError("Business does not belong to caller")

        normalized_root = normalize_root_url(root_url)
        discovery_url = well_known_url(normalized_root)
        domain = domain_from_url(normalized_root)

        owns_client = http_client is None
        client = http_client or httpx.AsyncClient()
        try:
            response = await client.get(discovery_url)
            response.raise_for_status()
            profile = response.json()
        except httpx.HTTPError as exc:
            raise MerchantRegistrationError(f"Failed to fetch UCP profile: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if not isinstance(profile, dict):
            raise MerchantRegistrationError("UCP profile response is not a JSON object")

        ucp_base_url = extract_rest_endpoint(profile)
        capabilities = extract_capabilities(profile)

        updates: dict[str, Any] = {
            "status": "active",
            "well_known_url": discovery_url,
            "ucp_base_url": ucp_base_url,
            "ucp_capabilities": capabilities,
        }
        if ucp_inbound_api_key:
            updates["encrypted_ucp_api_key"] = ucp_inbound_api_key.strip()

        await self._supabase.update(
            "businesses",
            updates,
            query={"id": f"eq.{business_id}"},
        )

        await self._supabase.upsert(
            "merchant_domains",
            {
                "business_id": business_id,
                "domain": domain,
                "verified": True,
            },
            on_conflict="domain",
        )

        return {
            "business_id": business_id,
            "root_url": normalized_root,
            "well_known_url": discovery_url,
            "ucp_base_url": ucp_base_url,
            "domain": domain,
            "capabilities": capabilities,
            "status": "active",
        }

    async def register(
        self,
        *,
        owner_id: str,
        name: str,
        category: str | None,
        root_url: str,
        ucp_inbound_api_key: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> dict[str, Any]:
        normalized_root = normalize_root_url(root_url)
        discovery_url = well_known_url(normalized_root)
        domain = domain_from_url(normalized_root)

        owns_client = http_client is None
        client = http_client or httpx.AsyncClient()
        try:
            response = await client.get(discovery_url)
            response.raise_for_status()
            profile = response.json()
        except httpx.HTTPError as exc:
            raise MerchantRegistrationError(f"Failed to fetch UCP profile: {exc}") from exc
        finally:
            if owns_client:
                await client.aclose()

        if not isinstance(profile, dict):
            raise MerchantRegistrationError("UCP profile response is not a JSON object")

        ucp_base_url = extract_rest_endpoint(profile)
        capabilities = extract_capabilities(profile)

        business_payload: dict[str, Any] = {
            "owner_id": owner_id,
            "name": name,
            "category": category,
            "status": "active",
            "well_known_url": discovery_url,
            "ucp_base_url": ucp_base_url,
            "ucp_capabilities": capabilities,
        }
        if ucp_inbound_api_key:
            business_payload["encrypted_ucp_api_key"] = ucp_inbound_api_key.strip()

        business_result = await self._supabase.insert("businesses", business_payload)
        business_row = _first_row(business_result)
        if business_row is None:
            raise MerchantRegistrationError("Business insert did not return a row")
        business_id = _require_row_id(business_row, "Business")

        await self._supabase.insert(
            "merchant_domains",
            {
                "business_id": business_id,
                "domain": domain,
                "verified": True,
            },
        )

        sdk_key = await issue_api_key(
            self._supabase,
            "sdk",
            business_id=business_id,
            scopes=["payment:verify", "payment:accredit", "payment:release"],
            label=f"{name} SDK key",
        )

        return {
            "business_id": business_id,
            "root_url": normalized_root,
            "well_known_url": discovery_url,
            "ucp_base_url": ucp_base_url,
            "domain": domain,
            "capabilities": capabilities,
            "sdk_api_key": sdk_key.plaintext,
            "sdk_api_key_prefix": sdk_key.key_prefix,
        }
