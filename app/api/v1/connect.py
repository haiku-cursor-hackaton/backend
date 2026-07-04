from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.dependencies import get_current_dashboard_user, get_supabase_client
from app.db.supabase import SupabaseClient
from app.services.dashboard_auth import DashboardUser
from app.services.key_issuer import issue_api_key

router = APIRouter(prefix="/v1/connect", tags=["connect"])

MCP_SCOPES = [
    "catalog:read",
    "checkout:write",
    "purchase:execute",
    "order:read",
    "wallet:read",
]


class ConnectClientRequest(BaseModel):
    full_name: str | None = None
    country: str | None = None


@router.post("/client")
async def connect_client(
    body: ConnectClientRequest,
    user: Annotated[DashboardUser, Depends(get_current_dashboard_user)],
    supabase: Annotated[SupabaseClient, Depends(get_supabase_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    await supabase.upsert(
        "profiles",
        {
            "id": user.id,
            "account_type": "client",
            "full_name": body.full_name,
            "country": body.country,
        },
        on_conflict="id",
    )

    api_key = await issue_api_key(
        supabase,
        "mcp",
        profile_id=user.id,
        scopes=MCP_SCOPES,
        label="MCP client key",
    )

    public_base = settings.public_base_url.rstrip("/")
    mcp_path = settings.mcp_path if settings.mcp_path.startswith("/") else f"/{settings.mcp_path}"

    return {
        "profile_id": user.id,
        "email": user.email,
        "phone": user.phone,
        "mcp_url": f"{public_base}{mcp_path}",
        "mcp_api_key": api_key.plaintext,
        "mcp_api_key_prefix": api_key.key_prefix,
    }
