from __future__ import annotations

import asyncio
import re

import pytest

from app.auth.api_keys import (
    APIKeyAuthError,
    coerce_api_key_context,
    generate_api_key,
    get_api_key_context,
    hash_api_key,
    normalize_scopes,
)


def test_hash_api_key_is_stable() -> None:
    api_key = "gk_mcp_example-token"
    first = hash_api_key(api_key)
    second = hash_api_key(api_key)
    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)


def test_generate_api_key_format_and_prefix() -> None:
    generated = generate_api_key("mcp")

    assert generated.plaintext.startswith("gk_mcp_")
    assert re.fullmatch(r"gk_mcp_[A-Za-z0-9_-]+", generated.plaintext)
    assert generated.key_prefix == generated.plaintext[:16]
    assert generated.key_hash == hash_api_key(generated.plaintext)
    assert generated.key_type == "mcp"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (["catalog:read", "wallet:read"], ["catalog:read", "wallet:read"]),
        ({"catalog:read": True, "wallet:read": False}, ["catalog:read"]),
        ("catalog:read, wallet:read", ["catalog:read", "wallet:read"]),
        (None, []),
    ],
)
def test_normalize_scopes(value: object, expected: list[str]) -> None:
    assert normalize_scopes(value) == expected


def test_coerce_api_key_context_from_dict() -> None:
    context = coerce_api_key_context(
        {
            "id": "key-1",
            "profile_id": "profile-1",
            "business_id": "business-1",
            "key_type": "mcp",
            "scopes": "catalog:read,checkout:write",
            "status": "active",
            "account_type": "user",
        }
    )

    assert context is not None
    assert context.api_key_id == "key-1"
    assert context.profile_id == "profile-1"
    assert context.business_id == "business-1"
    assert context.key_type == "mcp"
    assert context.scopes == ["catalog:read", "checkout:write"]
    assert context.status == "active"
    assert context.account_type == "user"


def test_coerce_api_key_context_from_one_item_list() -> None:
    context = coerce_api_key_context([{"api_key_id": "key-2", "status": "active"}])

    assert context is not None
    assert context.api_key_id == "key-2"
    assert context.status == "active"


class FakeSupabase:
    def __init__(self, rpc_result: object) -> None:
        self.rpc_result = rpc_result
        self.rpc_calls: list[tuple[str, dict | None]] = []

    async def rpc(self, name: str, payload: dict | None = None) -> object:
        self.rpc_calls.append((name, payload))
        return self.rpc_result


def test_get_api_key_context_calls_rpc_and_returns_context() -> None:
    async def run() -> None:
        api_key = "gk_mcp_test-token"
        fake_supabase = FakeSupabase(
            [
                {
                    "api_key_id": "key-3",
                    "key_type": "mcp",
                    "profile_id": "profile-3",
                    "scopes": ["catalog:read"],
                    "status": "active",
                }
            ]
        )

        context = await get_api_key_context(fake_supabase, api_key, key_type="mcp")

        assert fake_supabase.rpc_calls == [
            (
                "get_api_key_context",
                {"p_key_hash": hash_api_key(api_key), "p_key_type": "mcp"},
            )
        ]
        assert context.api_key_id == "key-3"
        assert context.key_type == "mcp"
        assert context.profile_id == "profile-3"
        assert context.scopes == ["catalog:read"]
        assert context.status == "active"

    asyncio.run(run())


def test_get_api_key_context_inactive_raises() -> None:
    async def run() -> None:
        fake_supabase = FakeSupabase({"api_key_id": "key-4", "status": "inactive"})

        with pytest.raises(APIKeyAuthError) as exc_info:
            await get_api_key_context(fake_supabase, "gk_mcp_inactive", key_type="mcp")

        assert exc_info.value.code == "inactive"

    asyncio.run(run())


def test_get_api_key_context_revoked_at_raises() -> None:
    async def run() -> None:
        fake_supabase = FakeSupabase(
            {"api_key_id": "key-5", "status": "active", "revoked_at": "2026-07-04T00:00:00Z"}
        )

        with pytest.raises(APIKeyAuthError) as exc_info:
            await get_api_key_context(fake_supabase, "gk_sdk_revoked", key_type="sdk")

        assert exc_info.value.code == "revoked"

    asyncio.run(run())
