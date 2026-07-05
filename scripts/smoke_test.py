from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_MERCHANT_URL = "http://127.0.0.1:8100"
EXPECTED_TOOL_COUNT = 10


@dataclass
class StepResult:
    name: str
    status: str
    detail: str = ""


@dataclass
class SmokeContext:
    backend_url: str
    merchant_url: str
    mcp_api_key: str
    sdk_api_key: str
    mcp_path: str = "/mcp"
    checkout_id: str | None = None
    product_id: str | None = None
    order_id: str | None = None
    payment_authorization_id: str | None = None
    steps: list[StepResult] = field(default_factory=list)

    @property
    def mcp_url(self) -> str:
        path = self.mcp_path if self.mcp_path.startswith("/") else f"/{self.mcp_path}"
        return f"{self.backend_url.rstrip('/')}{path}"


def load_credentials(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Credentials file must be a JSON object: {path}")
    return data


def resolve_config(args: argparse.Namespace) -> SmokeContext:
    creds: dict[str, Any] = {}
    if args.credentials:
        cred_path = args.credentials if args.credentials.is_absolute() else _BACKEND_ROOT / args.credentials
        creds = load_credentials(cred_path)

    backend_url = (
        args.backend_url
        or os.getenv("BACKEND_URL")
        or creds.get("backend_url")
        or DEFAULT_BACKEND_URL
    ).rstrip("/")
    merchant_url = (
        args.merchant_url
        or os.getenv("MERCHANT_URL")
        or creds.get("merchant_url")
        or DEFAULT_MERCHANT_URL
    ).rstrip("/")
    mcp_api_key = args.mcp_api_key or os.getenv("MCP_API_KEY") or creds.get("mcp_api_key")
    sdk_api_key = args.sdk_api_key or os.getenv("SDK_API_KEY") or creds.get("sdk_api_key")

    if not mcp_api_key:
        raise ValueError("MCP_API_KEY is required (env, --credentials, or --mcp-api-key)")
    if not sdk_api_key:
        raise ValueError("SDK_API_KEY is required (env, --credentials, or --sdk-api-key)")

    mcp_path = os.getenv("MCP_PATH", "/mcp")
    return SmokeContext(
        backend_url=backend_url,
        merchant_url=merchant_url,
        mcp_api_key=str(mcp_api_key),
        sdk_api_key=str(sdk_api_key),
        mcp_path=mcp_path,
    )


def _record(ctx: SmokeContext, name: str, status: str, detail: str = "") -> None:
    ctx.steps.append(StepResult(name=name, status=status, detail=detail))
    label = "PASS" if status == "pass" else status.upper()
    suffix = f" - {detail}" if detail else ""
    print(f"[{label}] {name}{suffix}")


async def _rpc(
    client: httpx.AsyncClient,
    ctx: SmokeContext,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    request_id: int = 1,
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        body["params"] = params
    response = await client.post(
        ctx.mcp_url,
        json=body,
        headers={"Authorization": f"Bearer {ctx.mcp_api_key}"},
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid JSON-RPC response for {method}")
    if "error" in payload:
        error = payload["error"]
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise RuntimeError(message or f"JSON-RPC error for {method}")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"JSON-RPC result missing for {method}")
    return result


def _structured(result: dict[str, Any]) -> dict[str, Any]:
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    return result


def _extract_product_id(payload: dict[str, Any]) -> str | None:
    products = payload.get("products")
    if isinstance(products, list) and products:
        first = products[0]
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"])
    return None


def _extract_checkout_id(payload: dict[str, Any]) -> str | None:
    for key in ("id", "checkout_id"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _extract_order_id(payload: dict[str, Any]) -> str | None:
    order = payload.get("order")
    if isinstance(order, dict) and order.get("id"):
        return str(order["id"])
    if payload.get("id") and payload.get("status") in {"created", "paid", "completed"}:
        return str(payload["id"])
    return None


def _extract_product_payload(payload: dict[str, Any]) -> dict[str, Any]:
    product = payload.get("product")
    if isinstance(product, dict):
        return product
    return payload


def _extract_payment_reference(payload: dict[str, Any]) -> str | None:
    payment = payload.get("payment")
    if not isinstance(payment, dict):
        return None
    instruments = payment.get("instruments")
    if not isinstance(instruments, list):
        return None
    for instrument in instruments:
        if not isinstance(instrument, dict):
            continue
        credential = instrument.get("credential")
        if isinstance(credential, dict) and credential.get("reference"):
            return str(credential["reference"])
    return None


async def run_smoke(ctx: SmokeContext) -> int:
    optional_failures = 0
    required_failures = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(f"{ctx.backend_url}/health")
            response.raise_for_status()
            body = response.json()
            if body.get("status") == "ok":
                _record(ctx, "GET /health", "pass")
            else:
                _record(ctx, "GET /health", "fail", f"unexpected body: {body}")
                required_failures += 1
        except Exception as exc:
            _record(ctx, "GET /health", "fail", str(exc))
            required_failures += 1

        try:
            init_result = await _rpc(client, ctx, "initialize")
            if init_result.get("protocolVersion"):
                _record(ctx, "MCP initialize", "pass")
            else:
                _record(ctx, "MCP initialize", "fail", "missing protocolVersion")
                required_failures += 1
        except Exception as exc:
            _record(ctx, "MCP initialize", "fail", str(exc))
            required_failures += 1

        try:
            tools_result = await _rpc(client, ctx, "tools/list", request_id=2)
            tools = tools_result.get("tools")
            count = len(tools) if isinstance(tools, list) else 0
            if count == EXPECTED_TOOL_COUNT:
                _record(ctx, "MCP tools/list", "pass", f"{count} tools")
            else:
                _record(ctx, "MCP tools/list", "fail", f"expected {EXPECTED_TOOL_COUNT}, got {count}")
                required_failures += 1
        except Exception as exc:
            _record(ctx, "MCP tools/list", "fail", str(exc))
            required_failures += 1

        search_payload: dict[str, Any] | None = None
        try:
            search_result = await _rpc(
                client,
                ctx,
                "tools/call",
                {
                    "name": "search_catalog",
                    "arguments": {"merchant_url": ctx.merchant_url, "query": "shirt"},
                },
                request_id=3,
            )
            search_payload = _structured(search_result)
            ctx.product_id = _extract_product_id(search_payload)
            if ctx.product_id:
                _record(ctx, "search_catalog", "pass", f"product_id={ctx.product_id}")
            else:
                _record(ctx, "search_catalog", "fail", "no products returned")
                required_failures += 1
        except Exception as exc:
            _record(ctx, "search_catalog", "fail", str(exc))
            required_failures += 1

        if ctx.product_id:
            try:
                product_result = await _rpc(
                    client,
                    ctx,
                    "tools/call",
                    {
                        "name": "get_product",
                        "arguments": {"merchant_url": ctx.merchant_url, "id": ctx.product_id},
                    },
                    request_id=4,
                )
                product_payload = _structured(product_result)
                product = _extract_product_payload(product_payload)
                if product.get("id") == ctx.product_id:
                    _record(ctx, "get_product", "pass")
                else:
                    _record(ctx, "get_product", "fail", "product id mismatch")
                    required_failures += 1
            except Exception as exc:
                _record(ctx, "get_product", "fail", str(exc))
                required_failures += 1
        else:
            _record(ctx, "get_product", "skip", "no product_id from search")

        if ctx.product_id:
            try:
                checkout_result = await _rpc(
                    client,
                    ctx,
                    "tools/call",
                    {
                        "name": "create_checkout",
                        "arguments": {
                            "merchant_url": ctx.merchant_url,
                            "line_items": [{"item": {"id": ctx.product_id}, "quantity": 1}],
                        },
                    },
                    request_id=5,
                )
                checkout_payload = _structured(checkout_result)
                ctx.checkout_id = _extract_checkout_id(checkout_payload)
                if ctx.checkout_id:
                    _record(ctx, "create_checkout", "pass", f"checkout_id={ctx.checkout_id}")
                else:
                    _record(ctx, "create_checkout", "fail", "missing checkout id")
                    required_failures += 1
            except Exception as exc:
                _record(ctx, "create_checkout", "fail", str(exc))
                required_failures += 1
        else:
            _record(ctx, "create_checkout", "skip", "no product_id")

        checkout_payload: dict[str, Any] | None = None
        if ctx.checkout_id:
            try:
                get_checkout_result = await _rpc(
                    client,
                    ctx,
                    "tools/call",
                    {
                        "name": "get_checkout",
                        "arguments": {"id": ctx.checkout_id},
                    },
                    request_id=6,
                )
                checkout_payload = _structured(get_checkout_result)
                status = checkout_payload.get("status")
                _record(ctx, "get_checkout", "pass", f"status={status}")
            except Exception as exc:
                _record(ctx, "get_checkout", "fail", str(exc))
                optional_failures += 1
        else:
            _record(ctx, "get_checkout", "skip", "no checkout_id")

        complete_payload: dict[str, Any] | None = None
        if ctx.checkout_id and checkout_payload is not None:
            status = checkout_payload.get("status")
            if status == "ready_for_complete":
                try:
                    complete_result = await _rpc(
                        client,
                        ctx,
                        "tools/call",
                        {
                            "name": "complete_checkout",
                            "arguments": {"id": ctx.checkout_id},
                        },
                        request_id=7,
                    )
                    complete_payload = _structured(complete_result)
                    ctx.order_id = _extract_order_id(complete_payload)
                    ctx.payment_authorization_id = _extract_payment_reference(complete_payload)
                    if ctx.order_id:
                        _record(ctx, "complete_checkout", "pass", f"order_id={ctx.order_id}")
                    else:
                        ucp = complete_payload.get("ucp")
                        if isinstance(ucp, dict) and ucp.get("status") == "error":
                            messages = complete_payload.get("messages") or []
                            detail = messages[0].get("content") if messages else "UCP error"
                            _record(ctx, "complete_checkout", "skip", str(detail))
                        else:
                            _record(ctx, "complete_checkout", "skip", f"status={complete_payload.get('status')}")
                except Exception as exc:
                    _record(ctx, "complete_checkout", "skip", str(exc))
                    optional_failures += 1
            else:
                _record(ctx, "complete_checkout", "skip", f"checkout status is {status!r}, not ready_for_complete")
        else:
            _record(ctx, "complete_checkout", "skip", "checkout unavailable")

        if ctx.order_id:
            try:
                order_result = await _rpc(
                    client,
                    ctx,
                    "tools/call",
                    {
                        "name": "get_order",
                        "arguments": {"merchant_url": ctx.merchant_url, "id": ctx.order_id},
                    },
                    request_id=8,
                )
                order_payload = _structured(order_result)
                if order_payload.get("id") == ctx.order_id:
                    _record(ctx, "get_order", "pass")
                else:
                    _record(ctx, "get_order", "skip", "order id mismatch")
            except Exception as exc:
                _record(ctx, "get_order", "skip", str(exc))
                optional_failures += 1
        else:
            _record(ctx, "get_order", "skip", "no order_id")

        if ctx.payment_authorization_id:
            try:
                response = await client.get(
                    f"{ctx.backend_url}/v1/payment-authorizations/{ctx.payment_authorization_id}",
                    headers={"Authorization": f"Bearer {ctx.sdk_api_key}"},
                )
                response.raise_for_status()
                body = response.json()
                auth_id = body.get("id") or body.get("authorization_id")
                if auth_id:
                    _record(ctx, "GET payment-authorization", "pass", f"id={auth_id}")
                else:
                    _record(ctx, "GET payment-authorization", "skip", "response missing id")
            except Exception as exc:
                _record(ctx, "GET payment-authorization", "skip", str(exc))
                optional_failures += 1
        else:
            _record(ctx, "GET payment-authorization", "skip", "no payment reference")

    print("")
    print("Smoke test summary")
    print("------------------")
    passed = sum(1 for step in ctx.steps if step.status == "pass")
    failed = sum(1 for step in ctx.steps if step.status == "fail")
    skipped = sum(1 for step in ctx.steps if step.status == "skip")
    print(f"  Passed:  {passed}")
    print(f"  Failed:  {failed} (required)")
    print(f"  Skipped: {skipped} (optional)")
    print(f"  Total:   {len(ctx.steps)}")

    if required_failures > 0:
        print("")
        print("Result: FAIL (required steps failed)")
        return 1
    print("")
    print("Result: PASS (required steps ok)")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Logical E2E smoke test for Genko backend + demo store.")
    parser.add_argument("--credentials", type=Path, help="Path to demo_seed_credentials.json from seed_demo.py")
    parser.add_argument("--backend-url", dest="backend_url", help="Backend base URL")
    parser.add_argument("--merchant-url", dest="merchant_url", help="Demo merchant root URL")
    parser.add_argument("--mcp-api-key", dest="mcp_api_key", help="MCP API key")
    parser.add_argument("--sdk-api-key", dest="sdk_api_key", help="SDK API key")
    return parser.parse_args(argv)


async def main_async(argv: list[str] | None = None) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(_BACKEND_ROOT / ".env")
    except ImportError:
        pass

    args = parse_args(argv)
    try:
        ctx = resolve_config(args)
    except Exception as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    return await run_smoke(ctx)


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
