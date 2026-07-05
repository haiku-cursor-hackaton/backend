"""Seed multiple merchants on the Genko platform for catalog + checkout testing.

Registers each merchant in the manifest, issues one client MCP key (shop any store),
one SDK key per merchant, and resets the client wallet.

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts.seed_demo import (  # noqa: E402
    AdminAuthClient,
    _first_row,
    find_active_demo_key,
    load_manifest,
    reset_demo_wallet_state,
)
from scripts.seed_lithe import _ensure_lithe_business, _find_business_by_domain  # noqa: E402
from app.db.supabase import SupabaseClient  # noqa: E402
from app.services.key_issuer import issue_api_key  # noqa: E402
from app.services.merchant_registration import domain_from_url, normalize_root_url  # noqa: E402

DEFAULT_MANIFEST = _BACKEND_ROOT / "fixtures" / "multi_merchant_seed_manifest.json"
DEFAULT_OUTPUT = _BACKEND_ROOT.parent / "temp" / "multi_merchant_credentials.json"
DEFAULT_MCP_ENV = _BACKEND_ROOT.parent / "temp" / "genko_mcp.env"


@dataclass
class MerchantSeedResult:
    slug: str
    name: str
    category: str | None
    url: str
    business_id: str
    sdk_api_key: str
    vendor_api_key: str
    sample_products: list[dict[str, Any]]


@dataclass
class MultiSeedResult:
    backend_url: str
    mcp_url: str
    mcp_api_key: str
    client_profile_id: str
    merchants: list[MerchantSeedResult]


def _resolve_vendor_key(merchant_spec: dict[str, Any]) -> str:
    if merchant_spec.get("vendor_api_key"):
        return str(merchant_spec["vendor_api_key"]).strip()
    env_name = merchant_spec.get("vendor_api_key_env")
    if env_name:
        value = os.getenv(str(env_name), "").strip()
        if value:
            return value
    slug = str(merchant_spec.get("slug") or "store")
    return f"gk_vendor_{slug}_{secrets.token_hex(20)}"


async def _resolve_sdk_key(
    supabase: SupabaseClient,
    *,
    label: str,
    business_id: str,
    slug: str,
    sdk_scopes: list[str],
    stored_merchants: list[dict[str, Any]],
    rotate_keys: bool,
) -> tuple[str, str]:
    sdk_label = f"{label}:{slug}"
    stored_key: str | None = None
    for entry in stored_merchants:
        if str(entry.get("business_id")) == business_id and entry.get("sdk_api_key"):
            stored_key = str(entry["sdk_api_key"])
            break

    if rotate_keys:
        rows = await supabase.select(
            "api_keys",
            query={
                "business_id": f"eq.{business_id}",
                "key_type": "eq.sdk",
                "label": f"eq.{sdk_label}",
                "status": "eq.active",
                "select": "id",
            },
        )
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("id"):
                await supabase.update(
                    "api_keys",
                    {"status": "revoked", "revoked_at": datetime.now(timezone.utc).isoformat()},
                    query={"id": f"eq.{row['id']}"},
                )
        issued = await issue_api_key(
            supabase,
            "sdk",
            business_id=business_id,
            scopes=sdk_scopes,
            label=sdk_label,
        )
        return issued.plaintext, issued.key_prefix

    existing = await find_active_demo_key(
        supabase,
        label=sdk_label,
        key_type="sdk",
        business_id=business_id,
    )
    if existing is not None and stored_key:
        prefix = str(existing.get("key_prefix") or "")
        if stored_key.startswith(prefix):
            return stored_key, prefix
        raise RuntimeError(
            f"Stored SDK key for {slug} does not match Supabase. Rerun with --rotate-keys."
        )

    issued = await issue_api_key(
        supabase,
        "sdk",
        business_id=business_id,
        scopes=sdk_scopes,
        label=sdk_label,
    )
    return issued.plaintext, issued.key_prefix


async def _resolve_mcp_key(
    supabase: SupabaseClient,
    *,
    label: str,
    profile_id: str,
    mcp_scopes: list[str],
    stored_mcp_key: str | None,
    rotate_keys: bool,
) -> str:
    if rotate_keys:
        rows = await supabase.select(
            "api_keys",
            query={
                "profile_id": f"eq.{profile_id}",
                "key_type": "eq.mcp",
                "label": f"eq.{label}",
                "status": "eq.active",
                "select": "id",
            },
        )
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("id"):
                await supabase.update(
                    "api_keys",
                    {"status": "revoked", "revoked_at": datetime.now(timezone.utc).isoformat()},
                    query={"id": f"eq.{row['id']}"},
                )
        issued = await issue_api_key(
            supabase,
            "mcp",
            profile_id=profile_id,
            scopes=mcp_scopes,
            label=label,
        )
        return issued.plaintext

    existing = await find_active_demo_key(
        supabase,
        label=label,
        key_type="mcp",
        profile_id=profile_id,
    )
    if existing is not None:
        if stored_mcp_key:
            prefix = str(existing.get("key_prefix") or "")
            if stored_mcp_key.startswith(prefix):
                return stored_mcp_key
        raise RuntimeError(
            "Active MCP key exists but credentials file is missing or stale. "
            "Rerun with --rotate-keys."
        )

    issued = await issue_api_key(
        supabase,
        "mcp",
        profile_id=profile_id,
        scopes=mcp_scopes,
        label=label,
    )
    return issued.plaintext


async def seed_multi_merchant(
    *,
    manifest_path: Path,
    output_path: Path,
    backend_url: str,
    rotate_keys: bool = False,
) -> MultiSeedResult:
    manifest = load_manifest(manifest_path)
    client_spec = manifest["client"]
    wallet_spec = manifest["wallet"]
    label = manifest["api_key_label"]
    mcp_scopes = manifest["scopes"]["mcp"]
    sdk_scopes = manifest["scopes"]["sdk"]
    merchant_specs = manifest["merchants"]

    supabase_url = os.environ["SUPABASE_URL"]
    service_role = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    supabase = SupabaseClient(supabase_url, service_role)
    admin = AdminAuthClient(supabase_url, service_role)

    stored: dict[str, Any] = {}
    if output_path.is_file():
        try:
            stored = json.loads(output_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            stored = {}
    stored_merchants = stored.get("merchants") if isinstance(stored.get("merchants"), list) else []

    seed_password = os.getenv("MULTI_SEED_PASSWORD", "MultiSeed-ChangeMe-2026!")

    client_user = await admin.get_or_create_user(
        email=client_spec["email"],
        password=seed_password,
        user_metadata={"phone_number": client_spec.get("phone_number")},
    )
    client_id = str(client_user["id"])

    await supabase.upsert(
        "profiles",
        {
            "id": client_id,
            "account_type": client_spec["account_type"],
            "full_name": client_spec["full_name"],
        },
        on_conflict="id",
    )

    merchant_results: list[MerchantSeedResult] = []
    first_business_id: str | None = None

    for merchant_spec in merchant_specs:
        owner_spec = merchant_spec["owner"]
        owner_user = await admin.get_or_create_user(
            email=owner_spec["email"],
            password=seed_password,
        )
        owner_id = str(owner_user["id"])
        await supabase.upsert(
            "profiles",
            {
                "id": owner_id,
                "account_type": owner_spec["account_type"],
                "full_name": owner_spec["full_name"],
            },
            on_conflict="id",
        )

        root_url = normalize_root_url(merchant_spec["root_url"])
        vendor_key = _resolve_vendor_key(merchant_spec)

        business_id, merchant_url = await _ensure_lithe_business(
            supabase,
            owner_id=owner_id,
            name=merchant_spec["name"],
            category=merchant_spec.get("category"),
            root_url=root_url,
            ucp_inbound_api_key=vendor_key,
        )
        if first_business_id is None:
            first_business_id = business_id

        sdk_key, _ = await _resolve_sdk_key(
            supabase,
            label=label,
            business_id=business_id,
            slug=str(merchant_spec.get("slug") or domain_from_url(root_url)),
            sdk_scopes=sdk_scopes,
            stored_merchants=stored_merchants,
            rotate_keys=rotate_keys,
        )

        merchant_results.append(
            MerchantSeedResult(
                slug=str(merchant_spec.get("slug") or domain_from_url(root_url)),
                name=str(merchant_spec["name"]),
                category=merchant_spec.get("category"),
                url=merchant_url,
                business_id=business_id,
                sdk_api_key=sdk_key,
                vendor_api_key=vendor_key,
                sample_products=list(merchant_spec.get("sample_products") or []),
            )
        )

    assert first_business_id is not None
    await reset_demo_wallet_state(
        supabase,
        profile_id=client_id,
        business_id=first_business_id,
        wallet_spec=wallet_spec,
    )

    mcp_key = await _resolve_mcp_key(
        supabase,
        label=label,
        profile_id=client_id,
        mcp_scopes=mcp_scopes,
        stored_mcp_key=stored.get("mcp_api_key") if isinstance(stored.get("mcp_api_key"), str) else None,
        rotate_keys=rotate_keys,
    )

    backend_url = backend_url.rstrip("/")
    mcp_path = os.getenv("MCP_PATH", "/mcp")
    if not mcp_path.startswith("/"):
        mcp_path = f"/{mcp_path}"

    result = MultiSeedResult(
        backend_url=backend_url,
        mcp_url=f"{backend_url}{mcp_path}",
        mcp_api_key=mcp_key,
        client_profile_id=client_id,
        merchants=merchant_results,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_warning": "Plaintext API keys for local use. Do not commit.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backend_url": result.backend_url,
        "mcp_url": result.mcp_url,
        "mcp_api_key": result.mcp_api_key,
        "client_profile_id": result.client_profile_id,
        "wallet": wallet_spec,
        "default_merchant_url": merchant_results[0].url if merchant_results else None,
        "merchants": [
            {
                "slug": m.slug,
                "name": m.name,
                "category": m.category,
                "url": m.url,
                "business_id": m.business_id,
                "sdk_api_key": m.sdk_api_key,
                "vendor_api_key": m.vendor_api_key,
                "sample_products": m.sample_products,
                "platform_env": {
                    "UCP_PLATFORM_URL": result.backend_url,
                    "UCP_PLATFORM_API_KEY": m.sdk_api_key,
                },
                "store_env": {
                    "UCP_GATEWAY_API_KEY": m.vendor_api_key,
                },
            }
            for m in merchant_results
        ],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    DEFAULT_MCP_ENV.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_MCP_ENV.write_text(
        "\n".join(
            [
                f"GENKO_MCP_URL={result.mcp_url}",
                f"GENKO_MCP_API_KEY={result.mcp_api_key}",
                f"GENKO_MERCHANT_URL={merchant_results[0].url}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    await admin.close()
    return result


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(_BACKEND_ROOT / ".env")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="Seed multiple merchants on Genko platform.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--backend-url",
        default=os.getenv("PUBLIC_BASE_URL", "https://genko-platform-production.up.railway.app"),
    )
    parser.add_argument("--rotate-keys", action="store_true")
    args = parser.parse_args()

    if not os.getenv("SUPABASE_URL") or not os.getenv("SUPABASE_SERVICE_ROLE_KEY"):
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.", file=sys.stderr)
        return 1

    manifest_path = args.manifest if args.manifest.is_absolute() else _BACKEND_ROOT / args.manifest
    output_path = args.output if args.output.is_absolute() else _BACKEND_ROOT / args.output

    try:
        result = asyncio.run(
            seed_multi_merchant(
                manifest_path=manifest_path,
                output_path=output_path,
                backend_url=args.backend_url,
                rotate_keys=args.rotate_keys,
            )
        )
    except Exception as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1

    print("Multi-merchant seed complete.")
    print(f"  Backend URL:  {result.backend_url}")
    print(f"  MCP URL:      {result.mcp_url}")
    print(f"  MCP key:      {result.mcp_api_key[:16]}...")
    print(f"  Wallet:       ${int(load_manifest(manifest_path)['wallet']['available_minor']) / 100:.2f} USD")
    print(f"  Credentials:  {output_path}")
    print()
    print("Merchants:")
    for merchant in result.merchants:
        print(f"  - {merchant.name}: {merchant.url}")
        print(f"      business_id: {merchant.business_id}")
        print(f"      sdk prefix:  {merchant.sdk_api_key[:16]}...")
        if merchant.sample_products:
            samples = ", ".join(p.get("search_query", p.get("id", "?")) for p in merchant.sample_products[:3])
            print(f"      try search: {samples}")
    print()
    print("Codex / MCP bridge env written to:", DEFAULT_MCP_ENV)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
