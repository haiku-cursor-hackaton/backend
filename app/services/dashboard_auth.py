from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.db.supabase import SupabaseClient


class DashboardAuthError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class DashboardUser(BaseModel):
    id: str
    email: str | None = None
    phone: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def _coerce_phone(raw: dict[str, Any]) -> str | None:
    phone = raw.get("phone")
    if phone:
        return str(phone)
    metadata = raw.get("user_metadata")
    if isinstance(metadata, dict):
        phone_number = metadata.get("phone_number")
        if phone_number:
            return str(phone_number)
    return None


async def get_dashboard_user(supabase: SupabaseClient, bearer_token: str) -> DashboardUser:
    try:
        raw = await supabase.get_auth_user(bearer_token)
    except Exception as exc:
        raise DashboardAuthError("Invalid or expired bearer token") from exc

    if not isinstance(raw, dict):
        raise DashboardAuthError("Invalid auth user response")

    user_id = raw.get("id")
    if not user_id:
        raise DashboardAuthError("Auth user response missing id")

    email = raw.get("email")
    return DashboardUser(
        id=str(user_id),
        email=str(email) if email else None,
        phone=_coerce_phone(raw),
        raw=raw,
    )
