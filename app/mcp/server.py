from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request

from app.auth.api_keys import ApiKeyContext
from app.auth.scopes import (
    CATALOG_READ,
    CHECKOUT_WRITE,
    ORDER_READ,
    PURCHASE_EXECUTE,
    WALLET_READ,
    require_scope,
)
from app.config import Settings, get_settings
from app.db.supabase import SupabaseClient
from app.dependencies import get_current_mcp_context, get_supabase_client
from app.services.buyer import buyer_from_context, merge_buyer
from app.services.checkout_store import extract_total_minor, find_checkout, upsert_checkout_from_ucp
from app.services.commerce_discovery import discover_commerces
from app.services.merchant_resolver import (
    CapabilityError,
    MerchantResolutionError,
    ResolvedMerchant,
    ensure_capability,
    merchant_from_business_row,
    resolve_merchant,
)
from app.services.ucp_client import UcpRestClient
from app.services.usage_events import record_usage_event
from app.services.purchase_history import get_purchase_history
from app.services.user_profile import get_user_profile
from app.services.wallet_orchestrator import CompleteCheckoutOrchestrator

JSONRPC_VERSION = "2.0"


def _usage_event_fields_for_tool(
    *,
    tool_name: str,
    payload: Any,
    business_id: str | None,
    profile_id: str,
    api_key_id: str | None,
    status: str,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "operation": tool_name,
        "transport": "mcp",
        "status": status,
        "business_id": business_id,
        "profile_id": profile_id,
        "api_key_id": api_key_id,
    }
    if tool_name != "complete_checkout" or status != "success":
        return fields
    if not isinstance(payload, dict):
        return fields
    ucp = payload.get("ucp")
    if isinstance(ucp, dict) and ucp.get("status") == "error":
        fields["status"] = "error"
        return fields
    total_minor, _currency = extract_total_minor(payload)
    if total_minor > 0:
        fields["is_purchase"] = True
        fields["revenue_minor"] = total_minor
    order = payload.get("order")
    if isinstance(order, dict) and order.get("id"):
        fields["order_id"] = str(order["id"])
    return fields

CAPABILITY_CATALOG_SEARCH = "dev.ucp.shopping.catalog.search"
CAPABILITY_CATALOG_LOOKUP = "dev.ucp.shopping.catalog.lookup"
CAPABILITY_CHECKOUT = "dev.ucp.shopping.checkout"
CAPABILITY_ORDER = "dev.ucp.shopping.order"

TOOL_SCOPES: dict[str, str] = {
    "get_user_profile": WALLET_READ,
    "discover_commerces": CATALOG_READ,
    "search_catalog": CATALOG_READ,
    "lookup_catalog": CATALOG_READ,
    "get_product": CATALOG_READ,
    "create_checkout": CHECKOUT_WRITE,
    "get_checkout": CHECKOUT_WRITE,
    "update_checkout": CHECKOUT_WRITE,
    "cancel_checkout": CHECKOUT_WRITE,
    "complete_checkout": PURCHASE_EXECUTE,
    "get_order": ORDER_READ,
    "get_purchase_history": ORDER_READ,
}

TOOL_CAPABILITIES: dict[str, str] = {
    "search_catalog": CAPABILITY_CATALOG_SEARCH,
    "lookup_catalog": CAPABILITY_CATALOG_LOOKUP,
    "get_product": CAPABILITY_CATALOG_SEARCH,
    "create_checkout": CAPABILITY_CHECKOUT,
    "get_checkout": CAPABILITY_CHECKOUT,
    "update_checkout": CAPABILITY_CHECKOUT,
    "cancel_checkout": CAPABILITY_CHECKOUT,
    "complete_checkout": CAPABILITY_CHECKOUT,
    "get_order": CAPABILITY_ORDER,
}

_LINE_ITEMS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "item": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            "quantity": {"type": "integer", "minimum": 1},
        },
        "required": ["item", "quantity"],
    },
}
_BUYER_SCHEMA = {
    "type": "object",
    "properties": {
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "email": {"type": "string"},
        "phone_number": {"type": "string"},
    },
}
_CATALOG_FILTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "categories": {"type": "array", "items": {"type": "string"}},
        "price": {
            "type": "object",
            "properties": {
                "min": {"type": "integer"},
                "max": {"type": "integer"},
            },
        },
    },
}
_CATALOG_PAGINATION_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "minimum": 1},
        "cursor": {"type": "string"},
    },
}
_MERCHANT_URL_SCHEMA = {"type": "string", "description": "Merchant root or UCP REST base URL."}
_PLATFORM_PAGINATION_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "minimum": 1},
        "offset": {"type": "integer", "minimum": 0},
    },
}
_DISCOVER_FILTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "categories": {"type": "array", "items": {"type": "string"}},
    },
}
_PURCHASE_HISTORY_FILTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "merchant_url": _MERCHANT_URL_SCHEMA,
        "status": {"type": "string"},
        "created_from": {"type": "string", "description": "ISO 8601 timestamp (inclusive lower bound)."},
        "created_to": {"type": "string", "description": "ISO 8601 timestamp (inclusive upper bound)."},
    },
}


def _tool_defs() -> list[dict[str, Any]]:
    merchant_required = {"merchant_url": _MERCHANT_URL_SCHEMA}
    merchant_optional = {"merchant_url": _MERCHANT_URL_SCHEMA}

    return [
        {
            "name": "get_user_profile",
            "description": "Return the signed-in client's non-sensitive profile and wallet balance.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "discover_commerces",
            "description": (
                "Discover registered platform merchants. Without a query, returns a feed of active "
                "merchants ordered by recency. With a query, searches name, description, and category."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "filters": _DISCOVER_FILTERS_SCHEMA,
                    "pagination": _PLATFORM_PAGINATION_SCHEMA,
                },
            },
        },
        {
            "name": "search_catalog",
            "description": "Search the merchant catalog (UCP catalog search).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **merchant_required,
                    "query": {"type": "string"},
                    "filters": _CATALOG_FILTERS_SCHEMA,
                    "pagination": _CATALOG_PAGINATION_SCHEMA,
                },
                "required": ["merchant_url"],
            },
        },
        {
            "name": "lookup_catalog",
            "description": "Resolve specific products by id (UCP catalog lookup).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **merchant_required,
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "filters": _CATALOG_FILTERS_SCHEMA,
                },
                "required": ["merchant_url", "ids"],
            },
        },
        {
            "name": "get_product",
            "description": "Get the full detail of a single product by id.",
            "inputSchema": {
                "type": "object",
                "properties": {**merchant_required, "id": {"type": "string"}},
                "required": ["merchant_url", "id"],
            },
        },
        {
            "name": "create_checkout",
            "description": "Create a checkout session for one or more line items.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **merchant_required,
                    "line_items": _LINE_ITEMS_SCHEMA,
                    "buyer": _BUYER_SCHEMA,
                },
                "required": ["merchant_url", "line_items"],
            },
        },
        {
            "name": "get_checkout",
            "description": "Get the current state of a checkout session.",
            "inputSchema": {
                "type": "object",
                "properties": {**merchant_optional, "id": {"type": "string"}},
                "required": ["id"],
            },
        },
        {
            "name": "update_checkout",
            "description": "Update line items and/or buyer details on a checkout session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    **merchant_optional,
                    "id": {"type": "string"},
                    "line_items": _LINE_ITEMS_SCHEMA,
                    "buyer": _BUYER_SCHEMA,
                },
                "required": ["id"],
            },
        },
        {
            "name": "complete_checkout",
            "description": "Finalize a checkout session and place the order.",
            "inputSchema": {
                "type": "object",
                "properties": {**merchant_optional, "id": {"type": "string"}},
                "required": ["id"],
            },
        },
        {
            "name": "cancel_checkout",
            "description": "Cancel a checkout session.",
            "inputSchema": {
                "type": "object",
                "properties": {**merchant_optional, "id": {"type": "string"}},
                "required": ["id"],
            },
        },
        {
            "name": "get_order",
            "description": "Get the current snapshot of a previously placed order by id.",
            "inputSchema": {
                "type": "object",
                "properties": {**merchant_required, "id": {"type": "string"}},
                "required": ["merchant_url", "id"],
            },
        },
        {
            "name": "get_purchase_history",
            "description": (
                "List the signed-in client's purchase history from the platform, with optional filters "
                "by merchant, order status, and date range."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "filters": _PURCHASE_HISTORY_FILTERS_SCHEMA,
                    "pagination": _PLATFORM_PAGINATION_SCHEMA,
                },
            },
        },
    ]


def _rpc_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def _tool_output(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "structuredContent": payload,
        "content": [{"type": "text", "text": json.dumps(payload)}],
    }


def _ucp_business_error(code: str, message: str) -> dict[str, Any]:
    return {
        "ucp": {"status": "error"},
        "messages": [{"code": code, "severity": "recoverable", "content": message}],
    }


def _catalog_payload(args: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if args.get("query") is not None:
        payload["query"] = args["query"]
    if args.get("filters") is not None:
        payload["filters"] = args["filters"]
    if args.get("pagination") is not None:
        payload["pagination"] = args["pagination"]
    return payload


def _platform_pagination(args: dict[str, Any]) -> tuple[int, int]:
    pagination = args.get("pagination") or {}
    limit = int(pagination.get("limit") or 20)
    offset = int(pagination.get("offset") or 0)
    return limit, offset


def _first_row(result: Any) -> dict[str, Any] | None:
    if isinstance(result, list):
        if not result:
            return None
        row = result[0]
        return row if isinstance(row, dict) else None
    if isinstance(result, dict):
        return result
    return None


class McpGateway:
    def __init__(
        self,
        *,
        supabase: SupabaseClient,
        settings: Settings,
        context: ApiKeyContext,
    ) -> None:
        self._supabase = supabase
        self._settings = settings
        self._context = context

    async def _resolve_merchant(self, tool_name: str, args: dict[str, Any]) -> ResolvedMerchant:
        merchant_url = args.get("merchant_url")
        if merchant_url:
            return await resolve_merchant(self._supabase, str(merchant_url))

        if tool_name in {"get_checkout", "update_checkout", "complete_checkout", "cancel_checkout"}:
            profile_id = self._context.profile_id or "unknown"
            checkout_id = str(args.get("id") or "")
            local = await find_checkout(
                self._supabase,
                profile_id=profile_id,
                external_checkout_id=checkout_id,
            )
            if local is None:
                raise ValueError("merchant_url is required")
            business = _first_row(
                await self._supabase.select(
                    "businesses",
                    query={
                        "id": f"eq.{local['business_id']}",
                        "select": "id,ucp_base_url,ucp_capabilities,encrypted_ucp_api_key",
                        "limit": "1",
                    },
                )
            )
            if business is None or not business.get("ucp_base_url"):
                raise MerchantResolutionError("Business UCP configuration not found")
            return merchant_from_business_row(business)

        raise ValueError("merchant_url is required")

    async def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in TOOL_SCOPES:
            raise ValueError(f"Unknown tool: {tool_name}")

        require_scope(self._context, TOOL_SCOPES[tool_name])

        profile_id = self._context.profile_id or "unknown"

        if tool_name == "get_user_profile":
            payload = await get_user_profile(self._supabase, self._context)
            await record_usage_event(
                self._supabase,
                operation=tool_name,
                transport="mcp",
                status="success",
                profile_id=profile_id,
                api_key_id=self._context.api_key_id,
            )
            return payload

        if tool_name == "discover_commerces":
            filters = args.get("filters") or {}
            limit, offset = _platform_pagination(args)
            payload = await discover_commerces(
                self._supabase,
                query=args.get("query"),
                categories=filters.get("categories"),
                limit=limit,
                offset=offset,
            )
            await record_usage_event(
                self._supabase,
                operation=tool_name,
                transport="mcp",
                status="success",
                profile_id=profile_id,
                api_key_id=self._context.api_key_id,
            )
            return payload

        if tool_name == "get_purchase_history":
            if not self._context.profile_id:
                raise ValueError("Purchase history is only available for client MCP API keys")
            filters = args.get("filters") or {}
            limit, offset = _platform_pagination(args)
            business_id: str | None = None
            merchant_url = filters.get("merchant_url")
            if merchant_url:
                try:
                    resolved = await resolve_merchant(self._supabase, str(merchant_url))
                    business_id = resolved.business_id
                except MerchantResolutionError as exc:
                    await record_usage_event(
                        self._supabase,
                        operation=tool_name,
                        transport="mcp",
                        status="error",
                        profile_id=profile_id,
                        api_key_id=self._context.api_key_id,
                    )
                    return _ucp_business_error(type(exc).__name__, exc.message)
            payload = await get_purchase_history(
                self._supabase,
                profile_id=profile_id,
                business_id=business_id,
                status=filters.get("status"),
                created_from=filters.get("created_from"),
                created_to=filters.get("created_to"),
                limit=limit,
                offset=offset,
            )
            await record_usage_event(
                self._supabase,
                operation=tool_name,
                transport="mcp",
                status="success",
                business_id=business_id,
                profile_id=profile_id,
                api_key_id=self._context.api_key_id,
            )
            return payload

        if tool_name in {"search_catalog", "lookup_catalog", "get_product", "create_checkout", "get_order"}:
            if not args.get("merchant_url"):
                raise ValueError("merchant_url is required")

        merchant: ResolvedMerchant | None = None
        ucp_client: UcpRestClient | None = None

        try:
            merchant = await self._resolve_merchant(tool_name, args)
            ensure_capability(merchant, TOOL_CAPABILITIES[tool_name])
            ucp_client = UcpRestClient(
                merchant.ucp_base_url,
                ucp_agent=self._settings.gateway_agent_name,
                merchant_api_key=merchant.inbound_api_key,
            )

            orchestrator = CompleteCheckoutOrchestrator(self._supabase, self._settings)

            if tool_name == "search_catalog":
                payload = await ucp_client.search_catalog(_catalog_payload(args))
            elif tool_name == "lookup_catalog":
                payload = await ucp_client.lookup_catalog(
                    {"ids": args["ids"], **({"filters": args["filters"]} if args.get("filters") else {})}
                )
            elif tool_name == "get_product":
                payload = await ucp_client.get_product({"id": args["id"]})
            elif tool_name == "create_checkout":
                body: dict[str, Any] = {"line_items": args["line_items"]}
                context_buyer = buyer_from_context(self._context, self._settings)
                body["buyer"] = merge_buyer(context_buyer, args.get("buyer"))
                payload = await ucp_client.create_checkout(body)
                await upsert_checkout_from_ucp(
                    self._supabase,
                    profile_id=profile_id,
                    business_id=merchant.business_id,
                    checkout_payload=payload,
                )
            elif tool_name == "get_checkout":
                payload = await ucp_client.get_checkout(args["id"])
                await upsert_checkout_from_ucp(
                    self._supabase,
                    profile_id=profile_id,
                    business_id=merchant.business_id,
                    checkout_payload=payload,
                )
            elif tool_name == "update_checkout":
                body: dict[str, Any] = {}
                if args.get("line_items") is not None:
                    body["line_items"] = args["line_items"]
                context_buyer = buyer_from_context(self._context, self._settings)
                body["buyer"] = merge_buyer(context_buyer, args.get("buyer"))
                payload = await ucp_client.update_checkout(args["id"], body)
                await upsert_checkout_from_ucp(
                    self._supabase,
                    profile_id=profile_id,
                    business_id=merchant.business_id,
                    checkout_payload=payload,
                )
            elif tool_name == "complete_checkout":
                payload = await orchestrator.complete(
                    ucp_client=ucp_client,
                    merchant=merchant,
                    profile_id=profile_id,
                    external_checkout_id=args["id"],
                )
            elif tool_name == "cancel_checkout":
                payload = await ucp_client.cancel_checkout(args["id"])
                await upsert_checkout_from_ucp(
                    self._supabase,
                    profile_id=profile_id,
                    business_id=merchant.business_id,
                    checkout_payload=payload,
                )
            elif tool_name == "get_order":
                payload = await ucp_client.get_order(args["id"])
            else:
                raise ValueError(f"Unknown tool: {tool_name}")

            await record_usage_event(
                self._supabase,
                **_usage_event_fields_for_tool(
                    tool_name=tool_name,
                    payload=payload,
                    business_id=merchant.business_id,
                    profile_id=profile_id,
                    api_key_id=self._context.api_key_id,
                    status="success",
                ),
            )
            return payload
        except (MerchantResolutionError, CapabilityError) as exc:
            await record_usage_event(
                self._supabase,
                operation=tool_name,
                transport="mcp",
                status="error",
                business_id=merchant.business_id if merchant else None,
                profile_id=profile_id,
                api_key_id=self._context.api_key_id,
            )
            return _ucp_business_error(type(exc).__name__, exc.message)
        finally:
            if ucp_client is not None:
                await ucp_client.close()


def build_mcp_router(*, path: str = "/mcp") -> APIRouter:
    router = APIRouter(tags=["mcp"])

    @router.post(path)
    async def mcp_endpoint(
        request: Request,
        context: ApiKeyContext = Depends(get_current_mcp_context),
        supabase: SupabaseClient = Depends(get_supabase_client),
        settings: Settings = Depends(get_settings),
    ) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            return _rpc_error(None, -32700, "Parse error")

        if not isinstance(body, dict):
            return _rpc_error(None, -32600, "Invalid Request")

        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}

        if method == "initialize":
            return _rpc_result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "genko", "version": "0.1.0"},
                },
            )

        if method == "tools/list":
            return _rpc_result(request_id, {"tools": _tool_defs()})

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}
            if isinstance(arguments, dict):
                arguments.pop("meta", None)
            else:
                return _rpc_error(request_id, -32602, "Invalid arguments: expected object")

            tool = str(tool_name or "")
            if tool not in TOOL_SCOPES:
                return _rpc_error(request_id, -32601, f"Unknown tool: {tool_name}")

            gateway = McpGateway(supabase=supabase, settings=settings, context=context)
            try:
                payload = await gateway.dispatch(tool, dict(arguments))
            except Exception as error:
                await record_usage_event(
                    supabase,
                    operation=str(tool_name),
                    transport="mcp",
                    status="error",
                    profile_id=context.profile_id,
                    api_key_id=context.api_key_id,
                )
                return _rpc_error(request_id, -32602, f"Invalid arguments: {error}")
            return _rpc_result(request_id, _tool_output(payload))

        return _rpc_error(request_id, -32601, f"Method not found: {method}")

    return router
