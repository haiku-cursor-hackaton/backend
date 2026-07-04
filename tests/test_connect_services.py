from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.auth.api_keys import hash_api_key
from app.services.dashboard_auth import DashboardAuthError, get_dashboard_user
from app.services.key_issuer import issue_api_key
from app.services.merchant_registration import (
    MerchantRegistrationError,
    MerchantRegistrationService,
    domain_from_url,
    extract_capabilities,
    extract_rest_endpoint,
    normalize_root_url,
    well_known_url,
)

SAMPLE_UCP_PROFILE = {
    "ucp": {
        "version": "2026-04-08",
        "services": {
            "dev.ucp.shopping": [
                {
                    "version": "2026-04-08",
                    "transport": "rest",
                    "endpoint": "https://store.example.com/ucp/v1",
                },
                {
                    "version": "2026-04-08",
                    "transport": "mcp",
                    "endpoint": "https://store.example.com/ucp/mcp",
                },
            ]
        },
        "capabilities": {
            "dev.ucp.shopping.checkout": [{"version": "2026-04-08"}],
            "dev.ucp.shopping.catalog.search": [{"version": "2026-04-08"}],
        },
    }
}


class FakeSupabase:
    def __init__(self) -> None:
        self.auth_user: dict[str, Any] | Exception | None = None
        self.insert_calls: list[tuple[str, dict[str, Any]]] = []
        self.upsert_calls: list[tuple[str, dict[str, Any], str | None]] = []
        self._insert_ids: dict[str, int] = {
            "businesses": 0,
            "merchant_domains": 0,
            "api_keys": 0,
        }

    async def get_auth_user(self, jwt: str) -> Any:
        if isinstance(self.auth_user, Exception):
            raise self.auth_user
        if self.auth_user is None:
            raise RuntimeError("auth user not configured")
        return self.auth_user

    async def insert(self, table: str, payload: dict[str, Any]) -> Any:
        self.insert_calls.append((table, dict(payload)))
        row = dict(payload)
        counter = self._insert_ids.get(table, 0) + 1
        self._insert_ids[table] = counter
        row["id"] = f"{table}-{counter}"
        return [row]

    async def upsert(
        self,
        table: str,
        payload: dict[str, Any],
        *,
        on_conflict: str | None = None,
    ) -> Any:
        self.upsert_calls.append((table, dict(payload), on_conflict))
        return [dict(payload)]


def test_normalize_root_url_strips_trailing_slash() -> None:
    assert normalize_root_url("https://store.example.com/") == "https://store.example.com"


def test_domain_from_url_extracts_host() -> None:
    assert domain_from_url("https://Store.Example.com/path") == "store.example.com"


def test_well_known_url() -> None:
    assert well_known_url("https://store.example.com/") == "https://store.example.com/.well-known/ucp"


def test_extract_rest_endpoint() -> None:
    assert extract_rest_endpoint(SAMPLE_UCP_PROFILE) == "https://store.example.com/ucp/v1"


def test_extract_capabilities() -> None:
    capabilities = extract_capabilities(SAMPLE_UCP_PROFILE)
    assert "dev.ucp.shopping.checkout" in capabilities
    assert "dev.ucp.shopping.catalog.search" in capabilities


def test_extract_rest_endpoint_missing_raises() -> None:
    with pytest.raises(MerchantRegistrationError):
        extract_rest_endpoint({"ucp": {"services": {"dev.ucp.shopping": []}}})


def test_get_dashboard_user_normalizes_fields() -> None:
    async def run() -> None:
        fake = FakeSupabase()
        fake.auth_user = {
            "id": "user-1",
            "email": "buyer@example.com",
            "user_metadata": {"phone_number": "+15551234567"},
        }

        user = await get_dashboard_user(fake, "jwt-token")

        assert user.id == "user-1"
        assert user.email == "buyer@example.com"
        assert user.phone == "+15551234567"

    asyncio.run(run())


def test_get_dashboard_user_invalid_token_raises() -> None:
    async def run() -> None:
        fake = FakeSupabase()
        fake.auth_user = httpx.HTTPStatusError(
            "Unauthorized",
            request=httpx.Request("GET", "https://example.com/auth/v1/user"),
            response=httpx.Response(401),
        )

        with pytest.raises(DashboardAuthError):
            await get_dashboard_user(fake, "bad-jwt")

    asyncio.run(run())


def test_issue_api_key_stores_hashed_key() -> None:
    async def run() -> None:
        fake = FakeSupabase()

        generated = await issue_api_key(
            fake,
            "mcp",
            profile_id="profile-1",
            scopes=["catalog:read"],
            label="test key",
        )

        assert generated.plaintext.startswith("gk_mcp_")
        assert fake.insert_calls == [
            (
                "api_keys",
                {
                    "key_type": "mcp",
                    "profile_id": "profile-1",
                    "business_id": None,
                    "key_hash": hash_api_key(generated.plaintext),
                    "key_prefix": generated.key_prefix,
                    "scopes": ["catalog:read"],
                    "status": "active",
                    "label": "test key",
                },
            )
        ]

    asyncio.run(run())


def test_merchant_registration_service_register() -> None:
    async def run() -> None:
        fake = FakeSupabase()
        service = MerchantRegistrationService(fake)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/.well-known/ucp")
            return httpx.Response(200, json=SAMPLE_UCP_PROFILE)

        transport = httpx.MockTransport(handler)
        http_client = httpx.AsyncClient(transport=transport)

        result = await service.register(
            owner_id="owner-1",
            name="Demo Store",
            category="retail",
            root_url="https://store.example.com/",
            http_client=http_client,
        )

        assert result["business_id"] == "businesses-1"
        assert result["root_url"] == "https://store.example.com"
        assert result["well_known_url"] == "https://store.example.com/.well-known/ucp"
        assert result["ucp_base_url"] == "https://store.example.com/ucp/v1"
        assert result["domain"] == "store.example.com"
        assert result["capabilities"] == SAMPLE_UCP_PROFILE["ucp"]["capabilities"]
        assert result["sdk_api_key"].startswith("gk_sdk_")
        assert result["sdk_api_key_prefix"] == result["sdk_api_key"][:16]

        business_call = fake.insert_calls[0]
        assert business_call[0] == "businesses"
        assert business_call[1]["owner_id"] == "owner-1"
        assert business_call[1]["status"] == "active"

        domain_call = fake.insert_calls[1]
        assert domain_call[0] == "merchant_domains"
        assert domain_call[1]["business_id"] == "businesses-1"
        assert domain_call[1]["domain"] == "store.example.com"
        assert domain_call[1]["verified"] is True

        sdk_call = fake.insert_calls[2]
        assert sdk_call[0] == "api_keys"
        assert sdk_call[1]["key_type"] == "sdk"
        assert sdk_call[1]["business_id"] == "businesses-1"
        assert sdk_call[1]["scopes"] == [
            "payment:verify",
            "payment:accredit",
            "payment:release",
        ]

    asyncio.run(run())
