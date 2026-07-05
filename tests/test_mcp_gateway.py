from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth.api_keys import ApiKeyContext
from app.auth.scopes import CATALOG_READ, CHECKOUT_WRITE, PURCHASE_EXECUTE
from app.config import Settings, get_settings
from app.dependencies import get_current_mcp_context, get_supabase_client
from app.main import app


MERCHANT_URL = "https://shop.example.com"
API_KEY = "gk_mcp_test-key"


class FakeSupabase:
    def __init__(self) -> None:
        self.usage_events: list[dict[str, Any]] = []
        self.checkouts: list[dict[str, Any]] = []

    async def rpc(self, name: str, payload: dict | None = None) -> Any:
        if name == "resolve_business_by_domain":
            return {
                "business_id": "biz-1",
                "ucp_base_url": "https://merchant.example",
                "ucp_capabilities": {
                    "dev.ucp.shopping.catalog.search": [{}],
                    "dev.ucp.shopping.catalog.lookup": [{}],
                    "dev.ucp.shopping.catalog.product": [{}],
                    "dev.ucp.shopping.checkout": [{}],
                    "dev.ucp.shopping.order": [{}],
                },
            }
        return {}

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        if table == "checkout_sessions":
            return self.checkouts[:1]
        if table == "businesses":
            return [
                {
                    "id": "biz-1",
                    "ucp_base_url": "https://merchant.example",
                    "ucp_capabilities": {
                        "dev.ucp.shopping.catalog.search": [{}],
                        "dev.ucp.shopping.catalog.lookup": [{}],
                        "dev.ucp.shopping.catalog.product": [{}],
                        "dev.ucp.shopping.checkout": [{}],
                        "dev.ucp.shopping.order": [{}],
                    },
                    "encrypted_ucp_api_key": None,
                }
            ]
        return []

    async def insert(self, table: str, payload: dict) -> Any:
        if table == "usage_events":
            self.usage_events.append(dict(payload))
            return [payload]
        if table == "checkout_sessions":
            row = dict(payload)
            row.setdefault("id", "local-chk-1")
            self.checkouts = [row]
            return [row]
        return [payload]

    async def update(self, table: str, payload: dict, *, query: dict[str, str]) -> Any:
        return [payload]

    async def close(self) -> None:
        return None


class FakeUcpClient:
    instances: list["FakeUcpClient"] = []

    def __init__(self, base_url: str, **kwargs: Any) -> None:
        self.base_url = base_url
        self.kwargs = kwargs
        FakeUcpClient.instances.append(self)

    async def search_catalog(self, payload: dict[str, Any] | None = None, **_: Any) -> dict[str, Any]:
        return {"products": [{"id": "sku-1", "title": "Demo"}], "query": (payload or {}).get("query")}

    async def close(self) -> None:
        return None


@pytest.fixture
def mcp_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    fake_supabase = FakeSupabase()
    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-key",
        gateway_agent_name="genko-gateway/0.1",
    )
    context = ApiKeyContext(
        api_key_id="key-1",
        profile_id="profile-1",
        scopes=[CATALOG_READ],
        raw={"email": "buyer@example.com", "phone_number": "+15551234567"},
    )

    app.dependency_overrides[get_supabase_client] = lambda: fake_supabase
    app.dependency_overrides[get_current_mcp_context] = lambda: context
    app.dependency_overrides[get_settings] = lambda: settings

    monkeypatch.setattr("app.mcp.server.UcpRestClient", FakeUcpClient)
    monkeypatch.setattr("app.services.wallet_orchestrator.UcpRestClient", FakeUcpClient)

    client = TestClient(app)
    client.fake_supabase = fake_supabase  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()
    FakeUcpClient.instances.clear()


def _rpc(method: str, params: dict[str, Any] | None = None, *, request_id: int = 1) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def test_mcp_initialize(mcp_client: TestClient) -> None:
    response = mcp_client.post("/mcp", json=_rpc("initialize"), headers={"Authorization": f"Bearer {API_KEY}"})
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["serverInfo"] == {"name": "genko", "version": "0.1.0"}


def test_mcp_tools_list_returns_nine_public_tools(mcp_client: TestClient) -> None:
    response = mcp_client.post("/mcp", json=_rpc("tools/list"), headers={"Authorization": f"Bearer {API_KEY}"})
    assert response.status_code == 200
    tools = response.json()["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == {
        "search_catalog",
        "lookup_catalog",
        "get_product",
        "create_checkout",
        "get_checkout",
        "update_checkout",
        "complete_checkout",
        "cancel_checkout",
        "get_order",
    }


def test_mcp_search_catalog_dual_output(mcp_client: TestClient) -> None:
    response = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "search_catalog",
                "arguments": {"merchant_url": MERCHANT_URL, "query": "shirt"},
            },
        ),
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert "error" not in body
    result = body["result"]
    assert "structuredContent" in result
    assert "content" in result
    assert result["structuredContent"]["products"][0]["id"] == "sku-1"
    assert result["content"][0]["type"] == "text"

    events = mcp_client.fake_supabase.usage_events  # type: ignore[attr-defined]
    assert events[-1]["operation"] == "search_catalog"
    assert events[-1]["transport"] == "mcp"
    assert events[-1]["status"] == "success"


def test_mcp_unknown_tool_returns_32601(mcp_client: TestClient) -> None:
    response = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "not_a_tool", "arguments": {}}),
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32601
    assert "Unknown tool" in error["message"]


def test_mcp_missing_checkout_id_returns_32602_not_unknown_tool(mcp_client: TestClient) -> None:
    app.dependency_overrides[get_current_mcp_context] = lambda: ApiKeyContext(
        api_key_id="key-1",
        profile_id="profile-1",
        scopes=[PURCHASE_EXECUTE, CHECKOUT_WRITE],
        raw={"email": "buyer@example.com"},
    )
    response = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "complete_checkout", "arguments": {}}),
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    assert response.status_code == 200
    error = response.json()["error"]
    assert error["code"] == -32602
    assert "Unknown tool" not in error["message"]
