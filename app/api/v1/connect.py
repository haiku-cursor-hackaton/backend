from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.dependencies import get_current_dashboard_user, get_supabase_client
from app.db.supabase import SupabaseClient
from app.services.dashboard_auth import DashboardUser
from app.services.key_issuer import issue_api_key
from app.services.merchant_registration import (
    MerchantRegistrationError,
    MerchantRegistrationService,
)
from app.services.sdk_onboarding import build_sdk_install_prompt

router = APIRouter(prefix="/v1/connect", tags=["connect"])

MCP_SCOPES = [
    "catalog:read",
    "checkout:write",
    "purchase:execute",
    "order:read",
    "wallet:read",
]

DEFAULT_MERCHANT_NAME = "Mi comercio"


class ConnectClientRequest(BaseModel):
    full_name: str | None = None
    country: str | None = None


class ConnectMerchantRequest(BaseModel):
    full_name: str | None = None
    business_name: str | None = None
    category: str | None = None
    description: str | None = None


def _metadata_name(user: DashboardUser) -> str | None:
    metadata = user.raw.get("user_metadata")
    if not isinstance(metadata, dict):
        return None
    name = metadata.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _profile_fields(
    user: DashboardUser,
    *,
    full_name: str | None,
    country: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"id": user.id}
    resolved_name = full_name if full_name is not None else _metadata_name(user)
    if resolved_name is not None:
        payload["full_name"] = resolved_name
    if country is not None:
        payload["country"] = country
    return payload


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
            "account_type": "client",
            **_profile_fields(user, full_name=body.full_name, country=body.country),
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


@router.post("/merchant")
async def connect_merchant(
    body: ConnectMerchantRequest,
    user: Annotated[DashboardUser, Depends(get_current_dashboard_user)],
    supabase: Annotated[SupabaseClient, Depends(get_supabase_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    await supabase.upsert(
        "profiles",
        {
            "account_type": "business",
            **_profile_fields(user, full_name=body.full_name),
        },
        on_conflict="id",
    )

    existing = await supabase.select(
        "businesses",
        query={
            "owner_id": f"eq.{user.id}",
            "select": "id,name,status,well_known_url",
            "order": "created_at.asc",
            "limit": "1",
        },
    )
    existing_row = existing[0] if isinstance(existing, list) and existing else None
    if existing_row and existing_row.get("id"):
        business_id = str(existing_row["id"])
        key_row = None
        key_rows = await supabase.select(
            "api_keys",
            query={
                "business_id": f"eq.{business_id}",
                "key_type": "eq.sdk",
                "status": "eq.active",
                "select": "id,key_prefix",
                "order": "created_at.desc",
                "limit": "1",
            },
        )
        if isinstance(key_rows, list) and key_rows:
            key_row = key_rows[0]

        return {
            "profile_id": user.id,
            "business_id": business_id,
            "status": existing_row.get("status") or "pending",
            "already_bootstrapped": True,
            "sdk_api_key": None,
            "sdk_api_key_prefix": (key_row or {}).get("key_prefix"),
            "sdk_install_prompt": None,
        }

    service = MerchantRegistrationService(supabase)
    name = (body.business_name or "").strip() or DEFAULT_MERCHANT_NAME
    try:
        result = await service.bootstrap_pending(
            owner_id=user.id,
            name=name,
            category=body.category,
            description=body.description,
        )
    except MerchantRegistrationError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    sdk_key = result["sdk_api_key"]
    return {
        "profile_id": user.id,
        "already_bootstrapped": False,
        "sdk_install_prompt": build_sdk_install_prompt(
            sdk_api_key=sdk_key,
            platform_url=settings.public_base_url,
        ),
        **result,
    }
