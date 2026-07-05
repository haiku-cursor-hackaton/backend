"""Seed Lithe production on the Genko platform (merchant + user MCP keys).

Creates or refreshes:
- client profile + wallet + MCP key (for agents)
- Lithe business registration + SDK key (for Lithe UCP_PLATFORM_API_KEY)
- stores vendor inbound key for UCP REST calls to Lithe

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in environment or .env.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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
    load_manifest,
    reset_demo_wallet_state,
    resolve_demo_api_keys,
)
from app.db.supabase import SupabaseClient  # noqa: E402
from app.services.merchant_registration import (  # noqa: E402
    MerchantRegistrationError,
    MerchantRegistrationService,
    domain_from_url,
    extract_capabilities,
    extract_rest_endpoint,
    normalize_root_url,
    well_known_url,
)

DEFAULT_MANIFEST = _BACKEND_ROOT / "fixtures" / "lithe_seed_manifest.json"
DEFAULT_OUTPUT = _BACKEND_ROOT.parent / "temp" / "lithe_credentials.json"
DEFAULT_VENDOR_KEY = "gk_vendor_lithe_76bb5e0d8ca429fe03f20834fc5147d12213c963"


@dataclass
class LitheSeedResult:
    backend_url: str
    mcp_url: str
    merchant_url: str
    mcp_api_key: str
    sdk_api_key: str
    business_id: str
    client_profile_id: str


async def _find_business_by_domain(supabase: SupabaseClient, domain: str) -> dict[str, Any] | None:
    domain_rows = await supabase.select(
        "merchant_domains",
        query={"domain": f"eq.{domain}", "select": "business_id", "limit": "1"},
    )
    domain_row = _first_row(domain_rows)
    if domain_row is None:
        return None
    business_rows = await supabase.select(
        "businesses",
        query={"id": f"eq.{domain_row['business_id']}", "select": "*", "limit": "1"},
    )
    return _first_row(business_rows)


async def _ensure_lithe_business(
    supabase: SupabaseClient,
    *,
    owner_id: str,
    name: str,
    category: str | None,
    root_url: str,
    ucp_inbound_api_key: str,
) -> tuple[str, str]:
    domain = domain_from_url(root_url)
    existing = await _find_business_by_domain(supabase, domain)
    if existing is not None:
        business_id = str(existing["id"])
        updates: dict[str, Any] = {
            "owner_id": owner_id,
            "name": name,
            "category": category,
            "status": "active",
            "well_known_url": well_known_url(root_url),
            "encrypted_ucp_api_key": ucp_inbound_api_key.strip(),
        }
        import httpx

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(well_known_url(root_url))
                response.raise_for_status()
                profile = response.json()
            if isinstance(profile, dict):
                updates["ucp_base_url"] = extract_rest_endpoint(profile)
                updates["ucp_capabilities"] = extract_capabilities(profile)
        except Exception as exc:
            print(f"[seed_lithe] Warning: could not refresh UCP profile: {exc}", file=sys.stderr)

        await supabase.update("businesses", updates, query={"id": f"eq.{business_id}"})
        return business_id, root_url

    service = MerchantRegistrationService(supabase)
    try:
        registration = await service.register(
            owner_id=owner_id,
            name=name,
            category=category,
            root_url=root_url,
            ucp_inbound_api_key=ucp_inbound_api_key,
        )
        return str(registration["business_id"]), str(registration["root_url"])
    except MerchantRegistrationError as exc:
        raise RuntimeError(f"Lithe registration failed: {exc.message}") from exc


async def seed_lithe(
    *,
    manifest_path: Path,
    output_path: Path,
    backend_url: str,
    vendor_key: str,
    rotate_keys: bool = False,
) -> LitheSeedResult:
    manifest = load_manifest(manifest_path)
    client_spec = manifest["client"]
    owner_spec = manifest["merchant_owner"]
    business_spec = manifest["business"]
    wallet_spec = manifest["wallet"]
    label = manifest["api_key_label"]
    mcp_scopes = manifest["scopes"]["mcp"]
    sdk_scopes = manifest["scopes"]["sdk"]

    supabase_url = os.environ["SUPABASE_URL"]
    service_role = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    supabase = SupabaseClient(supabase_url, service_role)
    admin = AdminAuthClient(supabase_url, service_role)

    demo_password = os.getenv("LITHE_SEED_PASSWORD", "LitheSeed-ChangeMe-2026!")
    root_url = normalize_root_url(business_spec["root_url"])

    client_user = await admin.get_or_create_user(
        email=client_spec["email"],
        password=demo_password,
        user_metadata={"phone_number": client_spec.get("phone_number")},
    )
    owner_user = await admin.get_or_create_user(
        email=owner_spec["email"],
        password=demo_password,
    )
    client_id = str(client_user["id"])
    owner_id = str(owner_user["id"])

    await supabase.upsert(
        "profiles",
        {
            "id": client_id,
            "account_type": client_spec["account_type"],
            "full_name": client_spec["full_name"],
        },
        on_conflict="id",
    )
    await supabase.upsert(
        "profiles",
        {
            "id": owner_id,
            "account_type": owner_spec["account_type"],
            "full_name": owner_spec["full_name"],
        },
        on_conflict="id",
    )

    business_id, merchant_url = await _ensure_lithe_business(
        supabase,
        owner_id=owner_id,
        name=business_spec["name"],
        category=business_spec.get("category"),
        root_url=root_url,
        ucp_inbound_api_key=vendor_key,
    )

    await reset_demo_wallet_state(
        supabase,
        profile_id=client_id,
        business_id=business_id,
        wallet_spec=wallet_spec,
    )

    mcp_key, sdk_key, _ = await resolve_demo_api_keys(
        supabase,
        label=label,
        profile_id=client_id,
        business_id=business_id,
        mcp_scopes=mcp_scopes,
        sdk_scopes=sdk_scopes,
        output_path=output_path,
        rotate_keys=rotate_keys,
    )

    backend_url = backend_url.rstrip("/")
    mcp_path = os.getenv("MCP_PATH", "/mcp")
    if not mcp_path.startswith("/"):
        mcp_path = f"/{mcp_path}"

    result = LitheSeedResult(
        backend_url=backend_url,
        mcp_url=f"{backend_url}{mcp_path}",
        merchant_url=merchant_url,
        mcp_api_key=mcp_key.plaintext,
        sdk_api_key=sdk_key.plaintext,
        business_id=business_id,
        client_profile_id=client_id,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_warning": "Plaintext API keys for local use. Do not commit.",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "backend_url": result.backend_url,
        "mcp_url": result.mcp_url,
        "merchant_url": result.merchant_url,
        "mcp_api_key": result.mcp_api_key,
        "sdk_api_key": result.sdk_api_key,
        "business_id": result.business_id,
        "client_profile_id": result.client_profile_id,
        "lithe_platform_env": {
            "UCP_PLATFORM_URL": result.backend_url,
            "UCP_PLATFORM_API_KEY": result.sdk_api_key,
        },
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    await admin.close()
    return result


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(_BACKEND_ROOT / ".env")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="Seed Lithe on Genko platform.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--backend-url", default=os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument(
        "--vendor-key",
        default=os.getenv("LITHE_UCP_GATEWAY_API_KEY", DEFAULT_VENDOR_KEY),
        help="Lithe UCP_GATEWAY_API_KEY (vendor inbound key).",
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
            seed_lithe(
                manifest_path=manifest_path,
                output_path=output_path,
                backend_url=args.backend_url,
                vendor_key=args.vendor_key,
                rotate_keys=args.rotate_keys,
            )
        )
    except Exception as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1

    print("Lithe seeded successfully.")
    print(f"  Backend URL:   {result.backend_url}")
    print(f"  MCP URL:       {result.mcp_url}")
    print(f"  Merchant URL:  {result.merchant_url}")
    print(f"  MCP key prefix: {result.mcp_api_key[:16]}...")
    print(f"  SDK key prefix: {result.sdk_api_key[:16]}...")
    print(f"  Credentials:   {output_path}")
    print()
    print("Set on Lithe (Railway):")
    print(f"  UCP_PLATFORM_URL={result.backend_url}")
    print(f"  UCP_PLATFORM_API_KEY=<sdk key in credentials file>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
