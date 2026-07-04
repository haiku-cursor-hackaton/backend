from __future__ import annotations

from typing import Any

import httpx


class UcpRestClient:
    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        ucp_agent: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(base_url=self._base_url)
        self._default_ucp_agent = ucp_agent
        self._default_request_id = request_id
        self._default_idempotency_key = idempotency_key

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _build_headers(
        self,
        *,
        ucp_agent: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        agent = ucp_agent if ucp_agent is not None else self._default_ucp_agent
        req_id = request_id if request_id is not None else self._default_request_id
        idem_key = idempotency_key if idempotency_key is not None else self._default_idempotency_key
        if agent:
            headers["UCP-Agent"] = agent
        if req_id:
            headers["Request-Id"] = req_id
        if idem_key:
            headers["Idempotency-Key"] = idem_key
        return headers

    async def _json_response(self, response: httpx.Response) -> Any:
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    async def search_catalog(
        self,
        payload: dict[str, Any] | None = None,
        *,
        ucp_agent: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/catalog/search",
            json=payload or {},
            headers=self._build_headers(
                ucp_agent=ucp_agent,
                request_id=request_id,
                idempotency_key=idempotency_key,
            ),
        )
        return await self._json_response(response)

    async def lookup_catalog(
        self,
        payload: dict[str, Any],
        *,
        ucp_agent: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/catalog/lookup",
            json=payload,
            headers=self._build_headers(
                ucp_agent=ucp_agent,
                request_id=request_id,
                idempotency_key=idempotency_key,
            ),
        )
        return await self._json_response(response)

    async def get_product(
        self,
        payload: dict[str, Any],
        *,
        ucp_agent: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/catalog/product",
            json=payload,
            headers=self._build_headers(
                ucp_agent=ucp_agent,
                request_id=request_id,
                idempotency_key=idempotency_key,
            ),
        )
        return await self._json_response(response)

    async def create_checkout(
        self,
        payload: dict[str, Any],
        *,
        ucp_agent: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/checkout-sessions",
            json=payload,
            headers=self._build_headers(
                ucp_agent=ucp_agent,
                request_id=request_id,
                idempotency_key=idempotency_key,
            ),
        )
        return await self._json_response(response)

    async def get_checkout(
        self,
        checkout_id: str,
        *,
        ucp_agent: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.get(
            f"/checkout-sessions/{checkout_id}",
            headers=self._build_headers(
                ucp_agent=ucp_agent,
                request_id=request_id,
                idempotency_key=idempotency_key,
            ),
        )
        return await self._json_response(response)

    async def update_checkout(
        self,
        checkout_id: str,
        payload: dict[str, Any],
        *,
        ucp_agent: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.put(
            f"/checkout-sessions/{checkout_id}",
            json=payload,
            headers=self._build_headers(
                ucp_agent=ucp_agent,
                request_id=request_id,
                idempotency_key=idempotency_key,
            ),
        )
        return await self._json_response(response)

    async def complete_checkout(
        self,
        checkout_id: str,
        payload: dict[str, Any] | None = None,
        *,
        ucp_agent: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"/checkout-sessions/{checkout_id}/complete",
            json=payload or {},
            headers=self._build_headers(
                ucp_agent=ucp_agent,
                request_id=request_id,
                idempotency_key=idempotency_key,
            ),
        )
        return await self._json_response(response)

    async def cancel_checkout(
        self,
        checkout_id: str,
        payload: dict[str, Any] | None = None,
        *,
        ucp_agent: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"/checkout-sessions/{checkout_id}/cancel",
            json=payload or {},
            headers=self._build_headers(
                ucp_agent=ucp_agent,
                request_id=request_id,
                idempotency_key=idempotency_key,
            ),
        )
        return await self._json_response(response)

    async def get_order(
        self,
        order_id: str,
        *,
        ucp_agent: str | None = None,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.get(
            f"/orders/{order_id}",
            headers=self._build_headers(
                ucp_agent=ucp_agent,
                request_id=request_id,
                idempotency_key=idempotency_key,
            ),
        )
        return await self._json_response(response)
