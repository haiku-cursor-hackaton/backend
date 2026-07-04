from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.api_keys import APIKeyAuthError, ApiKeyContext, get_api_key_context
from app.config import Settings, get_settings
from app.db.supabase import SupabaseClient
from app.services.dashboard_auth import DashboardAuthError, DashboardUser, get_dashboard_user

_bearer_scheme = HTTPBearer(auto_error=False)


def get_supabase_client(settings: Settings = Depends(get_settings)) -> SupabaseClient:
    return SupabaseClient(
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )


async def get_current_dashboard_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    supabase: SupabaseClient = Depends(get_supabase_client),
) -> DashboardUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    try:
        return await get_dashboard_user(supabase, credentials.credentials)
    except DashboardAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
        ) from exc


async def get_current_mcp_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    supabase: SupabaseClient = Depends(get_supabase_client),
) -> ApiKeyContext:
    return await _resolve_api_key_context(credentials, supabase, key_type="mcp")


async def get_current_sdk_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    supabase: SupabaseClient = Depends(get_supabase_client),
) -> ApiKeyContext:
    return await _resolve_api_key_context(credentials, supabase, key_type="sdk")


async def _resolve_api_key_context(
    credentials: HTTPAuthorizationCredentials | None,
    supabase: SupabaseClient,
    *,
    key_type: str,
) -> ApiKeyContext:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )

    try:
        return await get_api_key_context(supabase, credentials.credentials, key_type=key_type)
    except APIKeyAuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
        ) from exc
