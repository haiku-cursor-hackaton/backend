from typing import Any

import httpx


class SupabaseClient:
    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        base = supabase_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=f"{base}/rest/v1",
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _parse_response(response: httpx.Response) -> Any:
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    async def rpc(self, name: str, payload: dict | None = None) -> Any:
        response = await self._client.post(f"/rpc/{name}", json=payload or {})
        return self._parse_response(response)

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        response = await self._client.get(f"/{table}", params=query)
        return self._parse_response(response)

    async def insert(self, table: str, payload: dict) -> Any:
        response = await self._client.post(f"/{table}", json=payload)
        return self._parse_response(response)

    async def update(self, table: str, payload: dict, *, query: dict[str, str]) -> Any:
        response = await self._client.patch(f"/{table}", json=payload, params=query)
        return self._parse_response(response)
