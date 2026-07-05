# AGENTS.md — Genko platform backend

## Supabase

https://supabase.com/dashboard/project/kfutwosjsossgqhnhjor

## Production deployment

| Service | URL |
| --- | --- |
| **genko-platform** (use this) | `https://genko-platform-production.up.railway.app` |
| Lithe | `https://lithe-production.up.railway.app` |
| Genko Gear | `https://genko-gear-production.up.railway.app` |
| Genko Basics | `https://genko-basics-production.up.railway.app` |
| Genko Pantry | `https://genko-pantry-production.up.railway.app` |

Railway project: **lithe** → service `genko-platform`.

## MCP gateway (12 tools)

Platform-native: `get_user_profile`, `discover_commerces`, `get_purchase_history`.

Merchant proxy (pass `merchant_url`): catalog + checkout + `get_order`.

Implementation: `app/mcp/server.py`, `app/services/commerce_discovery.py`,
`app/services/purchase_history.py`.

## Agent boundary

- **User agents:** `POST /mcp` on the platform with `gk_mcp_*` keys only.
- **Platform → store:** UCP REST on merchant `/ucp/v1/*` with the vendor inbound
  key stored at registration (`encrypted_ucp_api_key`).
- **Store → platform:** `GET/POST /v1/payment-authorizations/*` with `gk_sdk_*`.

Agents must **not** call merchant REST directly. The vendor key is not an agent
API key.

## Multi-merchant E2E

```powershell
python scripts/seed_multi_merchant.py --backend-url https://genko-platform-production.up.railway.app
python scripts/smoke_test.py --credentials ../temp/multi_merchant_credentials.json `
  --merchant-url https://lithe-production.up.railway.app
```

Credentials: `../temp/multi_merchant_credentials.json`, `../temp/genko_mcp.env`
(gitignored).

Lithe-only seed still available via `scripts/seed_lithe.py` →
`../temp/lithe_credentials.json`.
