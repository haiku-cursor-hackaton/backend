from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.db.supabase import SupabaseClient
from app.services.key_issuer import issue_api_key
from app.services.merchant_registration import (
    MerchantRegistrationError,
    MerchantRegistrationService,
    normalize_root_url,
)

DEFAULT_MANIFEST = _BACKEND_ROOT / "fixtures" / "demo_seed_manifest.json"
DEFAULT_OUTPUT = _BACKEND_ROOT.parent / "temp" / "demo_seed_credentials.json"
DEMO_NAME_PREFIX = "[DEMO]"


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Manifest must be a JSON object: {path}")
    return data


def _first_row(result: Any) -> dict[str, Any] | None:
    if isinstance(result, list):
        if not result:
            return None
        row = result[0]
        return row if isinstance(row, dict) else None
    if isinstance(result, dict):
        return result
    return None


def plan_seed_actions(manifest: dict[str, Any]) -> list[str]:
    client = manifest["client"]
    owner = manifest["merchant_owner"]
    business = manifest["business"]
    wallet = manifest["wallet"]
    label = manifest["api_key_label"]
    mcp_scopes = manifest["scopes"]["mcp"]
    sdk_scopes = manifest["scopes"]["sdk"]

    return [
        f"Create or fetch auth user: {client['email']} (client)",
        f"Create or fetch auth user: {owner['email']} (merchant owner)",
        f"Upsert profile for client ({client['full_name']}, account_type={client['account_type']})",
        f"Upsert profile for merchant owner ({owner['full_name']}, account_type={owner['account_type']})",
        (
            f"Upsert wallet for client: {wallet['available_minor']} "
            f"{wallet['currency']} available_minor (reserved={wallet['reserved_minor']})"
        ),
        (
            f"Register or reuse demo merchant '{business['name']}' at "
            f"{business['root_url']} (domain={business['domain']})"
        ),
        f"Revoke prior active API keys labeled '{label}' for client profile",
        f"Issue MCP API key for client (scopes: {', '.join(mcp_scopes)}, label={label})",
        f"Revoke prior active API keys labeled '{label}' for demo business",
        f"Issue SDK API key for business (scopes: {', '.join(sdk_scopes)}, label={label})",
        "Write credentials JSON to output path (contains plaintext keys once)",
    ]


async def revoke_demo_seed_keys(
    supabase: SupabaseClient,
    *,
    label: str,
    profile_id: str | None = None,
    business_id: str | None = None,
) -> int:
    query: dict[str, str] = {
        "label": f"eq.{label}",
        "status": "eq.active",
    }
    if profile_id is not None:
        query["profile_id"] = f"eq.{profile_id}"
    if business_id is not None:
        query["business_id"] = f"eq.{business_id}"

    rows = await supabase.select("api_keys", query={**query, "select": "id"})
    if not isinstance(rows, list) or not rows:
        return 0

    revoked_at = datetime.now(timezone.utc).isoformat()
    await supabase.update(
        "api_keys",
        {"status": "revoked", "revoked_at": revoked_at},
        query=query,
    )
    return len(rows)


class AdminAuthClient:
    def __init__(self, supabase_url: str, service_role_key: str) -> None:
        base = supabase_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=base,
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def get_or_create_user(
        self,
        *,
        email: str,
        password: str,
        user_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "email": email,
            "password": password,
            "email_confirm": True,
        }
        if user_metadata:
            payload["user_metadata"] = user_metadata

        response = await self._client.post("/auth/v1/admin/users", json=payload)
        if response.status_code in {200, 201}:
            body = response.json()
            if isinstance(body, dict) and body.get("id"):
                return body

        if response.status_code in {409, 422}:
            existing = await self._find_user_by_email(email)
            if existing is not None:
                return existing

        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or not body.get("id"):
            raise RuntimeError(f"Unexpected admin user response for {email}")
        return body

    async def _find_user_by_email(self, email: str) -> dict[str, Any] | None:
        page = 1
        per_page = 200
        while True:
            response = await self._client.get(
                "/auth/v1/admin/users",
                params={"page": page, "per_page": per_page},
            )
            response.raise_for_status()
            body = response.json()
            users = body.get("users") if isinstance(body, dict) else None
            if not isinstance(users, list) or not users:
                return None
            for user in users:
                if isinstance(user, dict) and user.get("email") == email:
                    return user
            if len(users) < per_page:
                return None
            page += 1


@dataclass
class SeedResult:
    supabase_url: str
    client_profile_id: str
    merchant_profile_id: str
    business_id: str
    domain: str
    merchant_url: str
    mcp_api_key: str
    mcp_api_key_prefix: str
    sdk_api_key: str
    sdk_api_key_prefix: str
    backend_url: str
    mcp_url: str
    planned_actions: list[str] = field(default_factory=list)


class DemoSeeder:
    def __init__(
        self,
        *,
        manifest: dict[str, Any],
        dry_run: bool,
        output_path: Path,
        supabase_url: str | None = None,
        service_role_key: str | None = None,
        backend_url: str | None = None,
        admin_client: AdminAuthClient | None = None,
        supabase: SupabaseClient | None = None,
    ) -> None:
        self._manifest = manifest
        self._dry_run = dry_run
        self._output_path = output_path
        self._supabase_url = supabase_url
        self._service_role_key = service_role_key
        self._backend_url = (backend_url or os.getenv("PUBLIC_BASE_URL") or "http://127.0.0.1:8000").rstrip("/")
        self._mcp_path = os.getenv("MCP_PATH", "/mcp")
        if not self._mcp_path.startswith("/"):
            self._mcp_path = f"/{self._mcp_path}"
        self._admin = admin_client
        self._supabase = supabase
        self._insert_calls = 0
        self._upsert_calls = 0
        self._update_calls = 0

    @property
    def write_count(self) -> int:
        return self._insert_calls + self._upsert_calls + self._update_calls

    async def run(self) -> SeedResult:
        actions = plan_seed_actions(self._manifest)
        if self._dry_run:
            for action in actions:
                print(f"[dry-run] {action}")
            return SeedResult(
                supabase_url=self._supabase_url or "(not configured)",
                client_profile_id="(dry-run)",
                merchant_profile_id="(dry-run)",
                business_id="(dry-run)",
                domain=self._manifest["business"]["domain"],
                merchant_url=self._manifest["business"]["root_url"],
                mcp_api_key="(dry-run)",
                mcp_api_key_prefix="(dry-run)",
                sdk_api_key="(dry-run)",
                sdk_api_key_prefix="(dry-run)",
                backend_url=self._backend_url,
                mcp_url=f"{self._backend_url}{self._mcp_path}",
                planned_actions=actions,
            )

        if not self._supabase_url or not self._service_role_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required for --apply")
        if self._supabase is None or self._admin is None:
            raise RuntimeError("Supabase and admin clients are required for --apply")

        label = self._manifest["api_key_label"]
        client_spec = self._manifest["client"]
        owner_spec = self._manifest["merchant_owner"]
        business_spec = self._manifest["business"]
        wallet_spec = self._manifest["wallet"]
        mcp_scopes = self._manifest["scopes"]["mcp"]
        sdk_scopes = self._manifest["scopes"]["sdk"]

        demo_password = os.getenv("DEMO_SEED_PASSWORD", "DemoSeed-ChangeMe-2026!")

        client_user = await self._admin.get_or_create_user(
            email=client_spec["email"],
            password=demo_password,
            user_metadata={"phone_number": client_spec.get("phone_number")},
        )
        owner_user = await self._admin.get_or_create_user(
            email=owner_spec["email"],
            password=demo_password,
        )

        client_id = str(client_user["id"])
        owner_id = str(owner_user["id"])

        await self._supabase.upsert(
            "profiles",
            {
                "id": client_id,
                "account_type": client_spec["account_type"],
                "full_name": client_spec["full_name"],
            },
            on_conflict="id",
        )
        self._upsert_calls += 1

        await self._supabase.upsert(
            "profiles",
            {
                "id": owner_id,
                "account_type": owner_spec["account_type"],
                "full_name": owner_spec["full_name"],
            },
            on_conflict="id",
        )
        self._upsert_calls += 1

        await self._supabase.upsert(
            "wallets",
            {
                "profile_id": client_id,
                "currency": wallet_spec["currency"],
                "available_minor": wallet_spec["available_minor"],
                "reserved_minor": wallet_spec["reserved_minor"],
            },
            on_conflict="profile_id",
        )
        self._upsert_calls += 1

        business_id, merchant_url = await self._ensure_demo_business(
            owner_id=owner_id,
            business_spec=business_spec,
        )

        await revoke_demo_seed_keys(self._supabase, label=label, profile_id=client_id)
        self._update_calls += 1

        mcp_key = await issue_api_key(
            self._supabase,
            "mcp",
            profile_id=client_id,
            scopes=mcp_scopes,
            label=label,
        )
        self._insert_calls += 1

        await revoke_demo_seed_keys(self._supabase, label=label, business_id=business_id)
        self._update_calls += 1

        sdk_key = await issue_api_key(
            self._supabase,
            "sdk",
            business_id=business_id,
            scopes=sdk_scopes,
            label=label,
        )
        self._insert_calls += 1

        result = SeedResult(
            supabase_url=self._supabase_url,
            client_profile_id=client_id,
            merchant_profile_id=owner_id,
            business_id=business_id,
            domain=business_spec["domain"],
            merchant_url=merchant_url,
            mcp_api_key=mcp_key.plaintext,
            mcp_api_key_prefix=mcp_key.key_prefix,
            sdk_api_key=sdk_key.plaintext,
            sdk_api_key_prefix=sdk_key.key_prefix,
            backend_url=self._backend_url,
            mcp_url=f"{self._backend_url}{self._mcp_path}",
            planned_actions=actions,
        )
        self._write_credentials(result)
        self._print_summary(result)
        return result

    async def _ensure_demo_business(
        self,
        *,
        owner_id: str,
        business_spec: dict[str, Any],
    ) -> tuple[str, str]:
        assert self._supabase is not None
        domain = business_spec["domain"]
        existing = await self._find_demo_business_by_domain(domain)
        if existing is not None:
            business_id = str(existing["id"])
            root_url = normalize_root_url(
                str(existing.get("well_known_url") or business_spec["root_url"]).replace("/.well-known/ucp", "")
            )
            return business_id, root_url

        service = MerchantRegistrationService(self._supabase)
        try:
            registration = await service.register(
                owner_id=owner_id,
                name=business_spec["name"],
                category=business_spec.get("category"),
                root_url=business_spec["root_url"],
            )
            self._insert_calls += 3
            business_id = str(registration["business_id"])
            await revoke_demo_seed_keys(
                self._supabase,
                label=f"{business_spec['name']} SDK key",
                business_id=business_id,
            )
            self._update_calls += 1
            return business_id, str(registration["root_url"])
        except MerchantRegistrationError:
            print(
                "[seed] Demo store not reachable; inserting business from manifest offline.",
                file=sys.stderr,
            )
            return await self._insert_demo_business_offline(
                owner_id=owner_id,
                business_spec=business_spec,
            )

    async def _insert_demo_business_offline(
        self,
        *,
        owner_id: str,
        business_spec: dict[str, Any],
    ) -> tuple[str, str]:
        assert self._supabase is not None
        root_url = normalize_root_url(business_spec["root_url"])
        well_known = business_spec.get("well_known_url") or f"{root_url}/.well-known/ucp"
        ucp_base_url = business_spec.get("ucp_base_url") or f"{root_url}/ucp/v1"
        domain = business_spec["domain"]

        business_result = await self._supabase.insert(
            "businesses",
            {
                "owner_id": owner_id,
                "name": business_spec["name"],
                "category": business_spec.get("category"),
                "status": "active",
                "well_known_url": well_known,
                "ucp_base_url": ucp_base_url,
                "ucp_capabilities": {},
            },
        )
        self._insert_calls += 1
        business_row = _first_row(business_result)
        if business_row is None or not business_row.get("id"):
            raise RuntimeError("Offline business insert did not return an id")
        business_id = str(business_row["id"])

        await self._supabase.insert(
            "merchant_domains",
            {
                "business_id": business_id,
                "domain": domain,
                "verified": True,
            },
        )
        self._insert_calls += 1
        return business_id, root_url

    async def _find_demo_business_by_domain(self, domain: str) -> dict[str, Any] | None:
        assert self._supabase is not None
        domain_rows = await self._supabase.select(
            "merchant_domains",
            query={
                "domain": f"eq.{domain}",
                "select": "business_id",
                "limit": "1",
            },
        )
        domain_row = _first_row(domain_rows)
        if domain_row is None:
            return None

        business_rows = await self._supabase.select(
            "businesses",
            query={
                "id": f"eq.{domain_row['business_id']}",
                "select": "id,name,well_known_url",
                "limit": "1",
            },
        )
        business = _first_row(business_rows)
        if business is None:
            return None
        name = str(business.get("name") or "")
        if not name.startswith(DEMO_NAME_PREFIX):
            raise RuntimeError(
                f"Domain {domain} is registered to non-demo business '{name}'. "
                "Resolve manually before seeding."
            )
        return business

    def _write_credentials(self, result: SeedResult) -> None:
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_warning": (
                "Contains plaintext API keys generated once by scripts/seed_demo.py. "
                "Do not commit this file. Rotate keys if exposed."
            ),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "supabase_url": result.supabase_url,
            "backend_url": result.backend_url,
            "mcp_url": result.mcp_url,
            "merchant_url": result.merchant_url,
            "domain": result.domain,
            "client_profile_id": result.client_profile_id,
            "merchant_profile_id": result.merchant_profile_id,
            "business_id": result.business_id,
            "mcp_api_key": result.mcp_api_key,
            "mcp_api_key_prefix": result.mcp_api_key_prefix,
            "sdk_api_key": result.sdk_api_key,
            "sdk_api_key_prefix": result.sdk_api_key_prefix,
        }
        with self._output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")

    def _print_summary(self, result: SeedResult) -> None:
        print("Demo seed applied successfully.")
        print(f"  Client profile:  {result.client_profile_id}")
        print(f"  Merchant owner:  {result.merchant_profile_id}")
        print(f"  Business:        {result.business_id} ({result.domain})")
        print(f"  MCP key prefix:  {result.mcp_api_key_prefix}")
        print(f"  SDK key prefix:  {result.sdk_api_key_prefix}")
        print(f"  Backend URL:     {result.backend_url}")
        print(f"  MCP URL:         {result.mcp_url}")
        print(f"  Merchant URL:    {result.merchant_url}")
        print(f"Full API keys written to: {self._output_path}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed synthetic demo data for Genko backend.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=None,
        help="Print planned actions without writing (default unless --apply).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply seed changes to Supabase (requires service role credentials).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Path to demo seed manifest JSON (default: {DEFAULT_MANIFEST}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Credentials output path (default: {DEFAULT_OUTPUT}).",
    )
    return parser.parse_args(argv)


async def main_async(argv: list[str] | None = None) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(_BACKEND_ROOT / ".env")
    except ImportError:
        pass

    args = parse_args(argv)
    dry_run = not args.apply if args.dry_run is None else (args.dry_run and not args.apply)
    if args.apply:
        dry_run = False

    manifest_path = args.manifest if args.manifest.is_absolute() else _BACKEND_ROOT / args.manifest
    output_path = args.output if args.output.is_absolute() else _BACKEND_ROOT / args.output

    try:
        manifest = load_manifest(manifest_path)
    except Exception as exc:
        print(f"Failed to load manifest: {exc}", file=sys.stderr)
        return 1

    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    admin_client: AdminAuthClient | None = None
    supabase: SupabaseClient | None = None

    try:
        if not dry_run:
            admin_client = AdminAuthClient(supabase_url or "", service_role_key or "")
            supabase = SupabaseClient(supabase_url or "", service_role_key or "")

        seeder = DemoSeeder(
            manifest=manifest,
            dry_run=dry_run,
            output_path=output_path,
            supabase_url=supabase_url,
            service_role_key=service_role_key,
            admin_client=admin_client,
            supabase=supabase,
        )
        await seeder.run()
        return 0
    except Exception as exc:
        print(f"Demo seed failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if admin_client is not None:
            await admin_client.close()
        if supabase is not None:
            await supabase.close()


def main() -> None:
    raise SystemExit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
