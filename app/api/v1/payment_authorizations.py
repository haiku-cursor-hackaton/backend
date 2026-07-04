from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.auth.api_keys import ApiKeyContext
from app.dependencies import get_current_sdk_context, get_supabase_client
from app.db.supabase import SupabaseClient
from app.services.payment_authorizations import PaymentAuthorizationError, PaymentAuthorizationService

router = APIRouter(prefix="/v1/payment-authorizations", tags=["payment-authorizations"])


class AccreditRequest(BaseModel):
    order_id: str
    amount_minor: int = Field(..., ge=0)
    currency: str


class ReleaseRequest(BaseModel):
    reason: str | None = None


def _get_service(supabase: SupabaseClient = Depends(get_supabase_client)) -> PaymentAuthorizationService:
    return PaymentAuthorizationService(supabase)


def _require_business_id(context: ApiKeyContext) -> str:
    if not context.business_id:
        raise HTTPException(status_code=403, detail="SDK API key is not linked to a business.")
    return context.business_id


@router.get("/{authorization_id}")
async def get_payment_authorization(
    authorization_id: str,
    context: Annotated[ApiKeyContext, Depends(get_current_sdk_context)],
    service: Annotated[PaymentAuthorizationService, Depends(_get_service)],
) -> dict:
    business_id = _require_business_id(context)
    try:
        return await service.get_authorization(authorization_id, business_id)
    except PaymentAuthorizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{authorization_id}/accredit")
async def accredit_payment_authorization(
    authorization_id: str,
    body: AccreditRequest,
    context: Annotated[ApiKeyContext, Depends(get_current_sdk_context)],
    service: Annotated[PaymentAuthorizationService, Depends(_get_service)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    _ = idempotency_key
    business_id = _require_business_id(context)
    try:
        return await service.accredit(
            authorization_id,
            business_id,
            order_id=body.order_id,
            amount_minor=body.amount_minor,
            currency=body.currency,
        )
    except PaymentAuthorizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/{authorization_id}/release")
async def release_payment_authorization(
    authorization_id: str,
    body: ReleaseRequest,
    context: Annotated[ApiKeyContext, Depends(get_current_sdk_context)],
    service: Annotated[PaymentAuthorizationService, Depends(_get_service)],
) -> dict:
    business_id = _require_business_id(context)
    try:
        return await service.release(authorization_id, business_id, reason=body.reason)
    except PaymentAuthorizationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
