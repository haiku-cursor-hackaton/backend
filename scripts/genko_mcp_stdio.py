"""Stdio MCP bridge for Cursor -> Genko HTTP JSON-RPC gateway."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "genko-bridge", "version": "0.1.0"}
DEFAULT_MERCHANT_URL = "http://127.0.0.1:8100"


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _rpc(id_: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "result": result}


def _rpc_error(id_: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def _inject_merchant_url(arguments: dict[str, Any], merchant_url: str) -> dict[str, Any]:
    merged = dict(arguments)
    if "merchant_url" not in merged:
        merged["merchant_url"] = merchant_url
    return merged


class GenkoBridge:
    def __init__(self, *, mcp_url: str, api_key: str, merchant_url: str) -> None:
        self._mcp_url = mcp_url.rstrip("/")
        self._merchant_url = merchant_url.rstrip("/")
        self._client = httpx.Client(
            timeout=60.0,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def close(self) -> None:
        self._client.close()

    def _post(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
        if params is not None:
            payload["params"] = params
        response = self._client.post(self._mcp_url, json=payload)
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            error = body["error"]
            raise RuntimeError(str(error.get("message") or error))
        return body.get("result") or {}

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._post("tools/list")
        tools = result.get("tools") or []
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        args = _inject_merchant_url(arguments, self._merchant_url)
        result = self._post("tools/call", {"name": name, "arguments": args})
        if isinstance(result, dict) and "structuredContent" in result:
            return result
        return {"structuredContent": result, "content": [{"type": "text", "text": json.dumps(result)}]}


def handle_request(bridge: GenkoBridge, message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}

    if method == "initialize":
        return _rpc(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": SERVER_INFO,
            },
        )

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        tools = bridge.list_tools()
        return _rpc(request_id, {"tools": tools})

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _rpc_error(request_id, -32602, "Invalid tool arguments")
        try:
            result = bridge.call_tool(name, arguments)
        except Exception as exc:
            return _rpc_error(request_id, -32000, str(exc))
        content = result.get("content") if isinstance(result, dict) else None
        structured = result.get("structuredContent") if isinstance(result, dict) else result
        if not isinstance(content, list):
            content = [{"type": "text", "text": json.dumps(structured, ensure_ascii=False)}]
        return _rpc(request_id, {"content": content, "structuredContent": structured})

    if method == "ping":
        return _rpc(request_id, {})

    return _rpc_error(request_id, -32601, f"Method not found: {method}")


def main() -> int:
    mcp_url = _env("GENKO_MCP_URL", "http://127.0.0.1:8000/mcp")
    api_key = _env("GENKO_MCP_API_KEY")
    merchant_url = os.getenv("GENKO_MERCHANT_URL", DEFAULT_MERCHANT_URL)

    bridge = GenkoBridge(mcp_url=mcp_url, api_key=api_key, merchant_url=merchant_url)
    try:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                response = _rpc_error(None, -32700, "Parse error")
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
                continue

            if not isinstance(message, dict):
                continue

            response = handle_request(bridge, message)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
    finally:
        bridge.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
