from __future__ import annotations

from app.auth.api_keys import APIKeyAuthError, ApiKeyContext

CATALOG_READ = "catalog:read"
CHECKOUT_WRITE = "checkout:write"
PURCHASE_EXECUTE = "purchase:execute"
ORDER_READ = "order:read"
WALLET_READ = "wallet:read"


def has_scope(context: ApiKeyContext, scope: str) -> bool:
    return scope in context.scopes


def require_scope(context: ApiKeyContext, scope: str) -> None:
    if not has_scope(context, scope):
        raise APIKeyAuthError(f"Missing required scope: {scope}", code="missing_scope")
