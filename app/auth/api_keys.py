from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

KeyType = Literal["mcp", "sdk"]


class APIKeyAuthError(Exception):
    def __init__(self, message: str, *, code: str = "invalid_key") -> None:
        self.message = message
        self.code = code
        super().__init__(message)


class ApiKeyContext(BaseModel):
    api_key_id: str | None = None
    key_type: str | None = None
    profile_id: str | None = None
    business_id: str | None = None
    scopes: list[str] = Field(default_factory=list)
    status: str | None = None
    revoked_at: str | None = None
    account_type: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class GeneratedAPIKey:
    plaintext: str
    key_hash: str
    key_prefix: str
    key_type: KeyType


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def generate_api_key(key_type: KeyType) -> GeneratedAPIKey:
    plaintext = f"gk_{key_type}_{secrets.token_urlsafe(32)}"
    return GeneratedAPIKey(
        plaintext=plaintext,
        key_hash=hash_api_key(plaintext),
        key_prefix=plaintext[:16],
        key_type=key_type,
    )


def normalize_scopes(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, dict):
        return [scope for scope, enabled in value.items() if enabled]
    if isinstance(value, list):
        return [str(scope) for scope in value if scope]
    return []


def _coerce_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def coerce_api_key_context(value: Any) -> ApiKeyContext | None:
    if value is None:
        return None
    if isinstance(value, list):
        if not value:
            return None
        value = value[0]
    if not isinstance(value, dict):
        return None

    raw = dict(value)
    return ApiKeyContext(
        api_key_id=_coerce_optional_str(raw.get("api_key_id") or raw.get("id")),
        key_type=_coerce_optional_str(raw.get("key_type")),
        profile_id=_coerce_optional_str(raw.get("profile_id")),
        business_id=_coerce_optional_str(raw.get("business_id")),
        scopes=normalize_scopes(raw.get("scopes")),
        status=_coerce_optional_str(raw.get("status")),
        revoked_at=_coerce_optional_str(raw.get("revoked_at")),
        account_type=_coerce_optional_str(raw.get("account_type")),
        raw=raw,
    )


def _validate_api_key_context(
    context: ApiKeyContext,
    *,
    key_type: KeyType | None,
) -> None:
    status = (context.status or "active").lower()
    if context.revoked_at:
        raise APIKeyAuthError("API key revoked", code="revoked")
    if status == "revoked":
        raise APIKeyAuthError("API key revoked", code="revoked")
    if status == "inactive":
        raise APIKeyAuthError("API key inactive", code="inactive")
    if status != "active":
        raise APIKeyAuthError(f"API key status invalid: {context.status}", code="inactive")

    if key_type is not None and context.key_type is not None and context.key_type != key_type:
        raise APIKeyAuthError("API key type mismatch", code="type_mismatch")


async def get_api_key_context(
    supabase: Any,
    api_key: str,
    key_type: KeyType | None = None,
) -> ApiKeyContext:
    payload: dict[str, Any] = {"p_key_hash": hash_api_key(api_key)}
    if key_type is not None:
        payload["p_key_type"] = key_type

    result = await supabase.rpc("get_api_key_context", payload)
    context = coerce_api_key_context(result)
    if context is None:
        raise APIKeyAuthError("Invalid API key", code="invalid_key")

    _validate_api_key_context(context, key_type=key_type)
    return context
