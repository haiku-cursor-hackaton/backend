# AGENTS.md — Genko platform backend

## Supabase

https://supabase.com/dashboard/project/kfutwosjsossgqhnhjor

## Production deployment

| Service | URL |
| --- | --- |
| **genko-platform** (use this) | `https://genko-platform-production.up.railway.app` |
| Lithe store | `https://lithe-production.up.railway.app` |

Railway project: **lithe** → service `genko-platform`.

## Agent boundary

- **User agents:** `POST /mcp` on the platform with `gk_mcp_*` keys only.
- **Platform → store:** UCP REST on merchant `/ucp/v1/*` with the vendor inbound
  key stored at registration (`encrypted_ucp_api_key`).
- **Store → platform:** `GET/POST /v1/payment-authorizations/*` with `gk_sdk_*`.

Agents must **not** call merchant REST directly. The vendor key is not an agent
API key.

## Lithe E2E

```powershell
python scripts/seed_lithe.py --backend-url https://genko-platform-production.up.railway.app
python scripts/smoke_test.py --credentials ../temp/lithe_credentials.json `
  --merchant-url https://lithe-production.up.railway.app
```

Credentials: `../temp/lithe_credentials.json` (gitignored).
