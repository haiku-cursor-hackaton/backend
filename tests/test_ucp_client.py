from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from app.services.ucp_client import UcpRestClient

BASE_URL = "https://merchant.example.com/ucp/v1"


def test_ucp_rest_client_all_operations() -> None:
    async def run() -> None:
        calls: list[tuple[str, str, str | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            headers = dict(request.headers)
            calls.append((request.method, path, headers.get("content-type")))
            if request.method == "POST" and path.endswith("/catalog/search"):
                return httpx.Response(200, json={"products": []})
            if request.method == "POST" and path.endswith("/catalog/lookup"):
                return httpx.Response(200, json={"items": []})
            if request.method == "POST" and path.endswith("/catalog/product"):
                return httpx.Response(200, json={"id": "prod-1"})
            if request.method == "POST" and path.endswith("/checkout-sessions"):
                return httpx.Response(201, json={"id": "chk-1"})
            if request.method == "GET" and path.endswith("/checkout-sessions/chk-1"):
                return httpx.Response(200, json={"id": "chk-1", "status": "open"})
            if request.method == "PUT" and path.endswith("/checkout-sessions/chk-1"):
                return httpx.Response(200, json={"id": "chk-1", "status": "updated"})
            if request.method == "POST" and path.endswith("/checkout-sessions/chk-1/complete"):
                return httpx.Response(200, json={"id": "chk-1", "status": "completed"})
            if request.method == "POST" and path.endswith("/checkout-sessions/chk-1/cancel"):
                return httpx.Response(200, json={"id": "chk-1", "status": "cancelled"})
            if request.method == "GET" and path.endswith("/orders/ord-1"):
                return httpx.Response(200, json={"id": "ord-1"})
            return httpx.Response(404, json={"error": "not found"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)
        ucp = UcpRestClient(
            BASE_URL,
            client=client,
            ucp_agent="genko-gateway/0.1",
            request_id="req-default",
            idempotency_key="idem-default",
        )

        search = await ucp.search_catalog({"query": "hoodie"})
        lookup = await ucp.lookup_catalog({"refs": ["prod-1"]})
        product = await ucp.get_product({"id": "prod-1"})
        created = await ucp.create_checkout({"line_items": []})
        checkout = await ucp.get_checkout("chk-1")
        updated = await ucp.update_checkout("chk-1", {"buyer": {"email": "a@b.com"}})
        completed = await ucp.complete_checkout("chk-1", {"payment": {}})
        cancelled = await ucp.cancel_checkout("chk-1")
        order = await ucp.get_order("ord-1")

        assert search == {"products": []}
        assert lookup == {"items": []}
        assert product == {"id": "prod-1"}
        assert created == {"id": "chk-1"}
        assert checkout == {"id": "chk-1", "status": "open"}
        assert updated == {"id": "chk-1", "status": "updated"}
        assert completed == {"id": "chk-1", "status": "completed"}
        assert cancelled == {"id": "chk-1", "status": "cancelled"}
        assert order == {"id": "ord-1"}

        assert [method for method, _, _ in calls] == [
            "POST",
            "POST",
            "POST",
            "POST",
            "GET",
            "PUT",
            "POST",
            "POST",
            "GET",
        ]
        assert all(path.endswith(suffix) for (_, path, _), suffix in zip(
            calls,
            [
                "/catalog/search",
                "/catalog/lookup",
                "/catalog/product",
                "/checkout-sessions",
                "/checkout-sessions/chk-1",
                "/checkout-sessions/chk-1",
                "/checkout-sessions/chk-1/complete",
                "/checkout-sessions/chk-1/cancel",
                "/orders/ord-1",
            ],
            strict=True,
        ))

        await ucp.close()

    asyncio.run(run())


def test_ucp_rest_client_raises_on_non_2xx() -> None:
    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={"error": "unavailable"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)
        ucp = UcpRestClient(BASE_URL, client=client)

        with pytest.raises(httpx.HTTPStatusError):
            await ucp.search_catalog({})

        await ucp.close()

    asyncio.run(run())


def test_ucp_rest_client_per_call_headers() -> None:
    async def run() -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["ucp_agent"] = request.headers.get("ucp-agent")
            captured["request_id"] = request.headers.get("request-id")
            captured["idempotency_key"] = request.headers.get("idempotency-key")
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(base_url=BASE_URL, transport=transport)
        ucp = UcpRestClient(BASE_URL, client=client, ucp_agent="default-agent")

        await ucp.create_checkout(
            {"line_items": []},
            ucp_agent="override-agent",
            request_id="req-123",
            idempotency_key="idem-456",
        )

        assert captured["ucp_agent"] == "override-agent"
        assert captured["request_id"] == "req-123"
        assert captured["idempotency_key"] == "idem-456"

        await ucp.close()

    asyncio.run(run())
