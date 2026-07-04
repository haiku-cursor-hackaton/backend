from typing import Any

import httpx


class SupabaseClient:
    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        base = supabase_url.rstrip("/")
        self._supabase_url = base
        self._service_role_key = service_role_key
        self._client = httpx.AsyncClient(
            base_url=f"{base}/rest/v1",
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
            },
        )
        self._auth_client = httpx.AsyncClient(
            base_url=base,
            headers={
                "apikey": service_role_key,
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()
        await self._auth_client.aclose()

    @staticmethod
    def _parse_response(response: httpx.Response) -> Any:
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    async def get_auth_user(self, jwt: str) -> Any:
        response = await self._auth_client.get(
            "/auth/v1/user",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        return self._parse_response(response)

    async def rpc(self, name: str, payload: dict | None = None) -> Any:
        response = await self._client.post(f"/rpc/{name}", json=payload or {})
        return self._parse_response(response)

    async def select(self, table: str, *, query: dict[str, str] | None = None) -> Any:
        response = await self._client.get(f"/{table}", params=query)
        return self._parse_response(response)

    async def insert(self, table: str, payload: dict) -> Any:
        response = await self._client.post(
            f"/{table}",
            json=payload,
            headers={"Prefer": "return=representation"},
        )
        return self._parse_response(response)

    async def upsert(
        self,
        table: str,
        payload: dict,
        *,
        on_conflict: str | None = None,
    ) -> Any:
        params: dict[str, str] = {}
        if on_conflict is not None:
            params["on_conflict"] = on_conflict
        response = await self._client.post(
            f"/{table}",
            json=payload,
            params=params or None,
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        )
        return self._parse_response(response)

    async def update(self, table: str, payload: dict, *, query: dict[str, str]) -> Any:
        response = await self._client.patch(
            f"/{table}",
            json=payload,
            params=query,
            headers={"Prefer": "return=representation"},
        )
        return self._parse_response(response)
