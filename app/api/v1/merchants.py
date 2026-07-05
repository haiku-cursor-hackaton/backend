from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import get_current_dashboard_user, get_supabase_client
from app.db.supabase import SupabaseClient
from app.services.dashboard_auth import DashboardUser
from app.services.merchant_registration import MerchantRegistrationError, MerchantRegistrationService

router = APIRouter(prefix="/v1/merchants", tags=["merchants"])


class RegisterMerchantRequest(BaseModel):
    name: str = Field(..., min_length=1)
    category: str | None = None
    root_url: str = Field(..., min_length=1)
    ucp_inbound_api_key: str | None = Field(
        default=None,
        min_length=1,
        description="Vendor API key Genko sends when calling this merchant's UCP REST API.",
    )


def _get_registration_service(
    supabase: SupabaseClient = Depends(get_supabase_client),
) -> MerchantRegistrationService:
    return MerchantRegistrationService(supabase)


@router.post("/register")
async def register_merchant(
    body: RegisterMerchantRequest,
    user: Annotated[DashboardUser, Depends(get_current_dashboard_user)],
    supabase: Annotated[SupabaseClient, Depends(get_supabase_client)],
    service: Annotated[MerchantRegistrationService, Depends(_get_registration_service)],
) -> dict:
    await supabase.upsert(
        "profiles",
        {
            "id": user.id,
            "account_type": "business",
        },
        on_conflict="id",
    )

    try:
        return await service.register(
            owner_id=user.id,
            name=body.name,
            category=body.category,
            root_url=body.root_url,
            ucp_inbound_api_key=body.ucp_inbound_api_key,
        )
    except MerchantRegistrationError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
