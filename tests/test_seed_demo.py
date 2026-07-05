from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from scripts.seed_demo import (
    AdminAuthClient,
    DemoSeeder,
    apply_runtime_overrides,
    load_manifest,
    plan_seed_actions,
    revoke_demo_seed_keys,
)

BACKEND_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = BACKEND_ROOT / "fixtures" / "demo_seed_manifest.json"


class FakeSupabase:
    def __init__(self) -> None:
        self.insert_calls: list[tuple[str, dict[str, Any]]] = []
        self.upsert_calls: list[tuple[str, dict[str, Any], str | None]] = []
        self.update_calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self.select_results: dict[str, Any] = {}

    async def insert(self, table: str, payload: dict[str, Any]) -> Any:
        self.insert_calls.append((table, dict(payload)))
        row = dict(payload)
        row["id"] = f"{table}-1"
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

    async def update(self, table: str, payload: dict[str, Any], *, query: dict[str, str]) -> Any:
        self.update_calls.append((table, dict(payload), dict(query)))
        return [{"id": "key-1"}]

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        if table == "api_keys":
            return self.select_results.get("api_keys", [])
        key = table
        if query:
            key = f"{table}:{json.dumps(query, sort_keys=True)}"
        result = self.select_results.get(key)
        if result is not None:
            return result
        return []

    async def close(self) -> None:
        return None


def test_manifest_loads() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    assert manifest["client"]["email"] == "demo-client@genko.local"
    assert manifest["merchant_owner"]["account_type"] == "business"
    assert manifest["business"]["root_url"] == "http://127.0.0.1:8111"
    assert manifest["wallet"]["available_minor"] == 2000
    assert manifest["api_key_label"] == "demo-seed-v1"
    assert "catalog:read" in manifest["scopes"]["mcp"]
    assert "payment:verify" in manifest["scopes"]["sdk"]


def test_plan_seed_actions_includes_core_steps() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    actions = plan_seed_actions(manifest)
    assert any("demo-client@genko.local" in action for action in actions)
    assert any("2000" in action and "USD" in action for action in actions)
    assert any("demo-seed-v1" in action for action in actions)


def test_apply_runtime_overrides_updates_demo_business_urls() -> None:
    manifest = load_manifest(MANIFEST_PATH)

    updated = apply_runtime_overrides(manifest, merchant_url="http://127.0.0.1:8100/")

    assert manifest["business"]["root_url"] == "http://127.0.0.1:8111"
    assert updated["business"]["root_url"] == "http://127.0.0.1:8100"
    assert updated["business"]["well_known_url"] == "http://127.0.0.1:8100/.well-known/ucp"
    assert updated["business"]["ucp_base_url"] == "http://127.0.0.1:8100/ucp/v1"
    assert updated["business"]["domain"] == "127.0.0.1"


def test_dry_run_performs_zero_writes() -> None:
    async def run() -> None:
        manifest = load_manifest(MANIFEST_PATH)
        fake = FakeSupabase()
        seeder = DemoSeeder(
            manifest=manifest,
            dry_run=True,
            output_path=BACKEND_ROOT / "temp" / "test_credentials.json",
            supabase=fake,
        )
        result = await seeder.run()
        assert result.client_profile_id == "(dry-run)"
        assert seeder.write_count == 0
        assert fake.insert_calls == []
        assert fake.upsert_calls == []
        assert fake.update_calls == []

    asyncio.run(run())


def test_revoke_demo_seed_keys_updates_active_keys() -> None:
    async def run() -> None:
        fake = FakeSupabase()
        fake.select_results["api_keys"] = [{"id": "key-1"}, {"id": "key-2"}]

        revoked = await revoke_demo_seed_keys(
            fake,
            label="demo-seed-v1",
            profile_id="profile-1",
        )

        assert revoked == 2
        assert len(fake.update_calls) == 1
        table, payload, query = fake.update_calls[0]
        assert table == "api_keys"
        assert payload["status"] == "revoked"
        assert "revoked_at" in payload
        assert query["label"] == "eq.demo-seed-v1"
        assert query["profile_id"] == "eq.profile-1"

    asyncio.run(run())


def test_admin_get_or_create_user_reuses_existing_on_conflict(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        calls: list[tuple[str, str, dict[str, Any] | None]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST" and request.url.path.endswith("/auth/v1/admin/users"):
                calls.append(("POST", request.url.path, json.loads(request.content.decode())))
                return httpx.Response(422, json={"msg": "User already registered"})
            if request.method == "GET" and request.url.path.endswith("/auth/v1/admin/users"):
                calls.append(("GET", request.url.path, dict(request.url.params)))
                return httpx.Response(
                    200,
                    json={
                        "users": [
                            {"id": "user-existing", "email": "demo-client@genko.local"},
                        ]
                    },
                )
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport, base_url="https://example.supabase.co")
        admin = AdminAuthClient("https://example.supabase.co", "service-role-key")
        admin._client = client

        user = await admin.get_or_create_user(
            email="demo-client@genko.local",
            password="secret",
            user_metadata={"phone_number": "+10000000001"},
        )

        assert user["id"] == "user-existing"
        assert calls[0][0] == "POST"
        assert calls[1][0] == "GET"
        await admin.close()

    asyncio.run(run())
