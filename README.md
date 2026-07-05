# Genko Backend

FastAPI backend for the Genko MCP gateway over UCP merchants.

## Requirements

- Python 3.10+

## Setup

1. Copy `.env.example` to `.env` and fill in your Supabase credentials.
2. Install dependencies (from the project root):

```powershell
pip install -e ".[dev]"
```

## Run locally

```powershell
uvicorn app.main:app --reload
```

The API listens on `http://127.0.0.1:8000` by default.

## Merchant registration (Lithe / protected UCP)

When a merchant requires inbound auth (for example Lithe with `UCP_GATEWAY_API_KEY`), register it with the same vendor key Genko must send on outbound UCP REST calls:

```powershell
# POST /v1/merchants/register (Supabase JWT bearer)
# Body:
# {
#   "name": "Lithe",
#   "root_url": "https://lithe-production.up.railway.app",
#   "ucp_inbound_api_key": "<same value as Lithe UCP_GATEWAY_API_KEY>"
# }
```

Genko stores the key in `businesses.encrypted_ucp_api_key` and sends `Authorization: Bearer ...` on every `UcpRestClient` call. Configure Lithe with the returned `sdk_api_key` as `UCP_PLATFORM_API_KEY`.

## Production deployment

The Genko platform is deployed on Railway in the **lithe** project as
`genko-platform`:

| | |
| --- | --- |
| **Platform URL** | `https://genko-platform-production.up.railway.app` |
| **Health** | `GET /health` |
| **Agent MCP** | `POST /mcp` (user `gk_mcp_*` key) |

Registered production merchants (REST-only UCP stores):

| Store | URL | Category |
| --- | --- | --- |
| Lithe | `https://lithe-production.up.railway.app` | apparel |
| Genko Gear | `https://genko-gear-production.up.railway.app` | home-goods |
| Genko Basics | `https://genko-basics-production.up.railway.app` | apparel |
| Genko Pantry | `https://genko-pantry-production.up.railway.app` | food |

### MCP tools (12)

Platform-native (Supabase — no `merchant_url` required):

| Tool | Purpose |
| --- | --- |
| `get_user_profile` | Signed-in client profile + wallet balance |
| `discover_commerces` | List/search registered merchants (`query`, `filters.categories`, pagination) |
| `get_purchase_history` | Client order history (`filters.merchant_url`, `status`, date range) |

Merchant proxy (require `merchant_url` on catalog/checkout/order tools):

| Tool | UCP REST |
| --- | --- |
| `search_catalog` | `POST /catalog/search` |
| `lookup_catalog` | `POST /catalog/lookup` |
| `get_product` | `POST /catalog/product` |
| `create_checkout` | `POST /checkout-sessions` |
| `get_checkout` | `GET /checkout-sessions/{id}` |
| `update_checkout` | `PUT /checkout-sessions/{id}` |
| `complete_checkout` | `POST /checkout-sessions/{id}/complete` |
| `cancel_checkout` | `POST /checkout-sessions/{id}/cancel` |
| `get_order` | `GET /orders/{id}` (when merchant advertises Order capability) |

**Multi-merchant agent flow:** `discover_commerces` → pick `merchant_url` →
`search_catalog` → checkout → `get_purchase_history`. One checkout session is
one merchant; multiple `line_items` within the same store are fine.

### Seed credentials

**Multi-merchant (recommended)** — one MCP key, four stores, $1,000 wallet:

```powershell
python scripts/seed_multi_merchant.py --backend-url https://genko-platform-production.up.railway.app
```

Writes `../temp/multi_merchant_credentials.json` and `../temp/genko_mcp.env`
(gitignored).

**Lithe-only:**

```powershell
python scripts/seed_lithe.py --backend-url https://genko-platform-production.up.railway.app
```

Writes `../temp/lithe_credentials.json`.

### Connect Codex (HTTP MCP)

In `%USERPROFILE%\.codex\config.toml`:

```toml
[mcp_servers.genko]
url = "https://genko-platform-production.up.railway.app/mcp"
bearer_token_env_var = "GENKO_MCP_API_KEY"
startup_timeout_sec = 20
tool_timeout_sec = 60
enabled = true
```

Set `GENKO_MCP_API_KEY` in Codex secrets (from the seed output). After a platform
deploy that adds tools, **disable and re-enable** the MCP server (or rename the
block) so Codex refreshes `tools/list` — the HTTP server does not yet advertise
`listChanged`.

**Stdio bridge (Cursor / local):** `scripts/genko_mcp_stdio.py` proxies
`tools/list` from the platform and injects a default `merchant_url` when omitted.
Use `scripts/genko_mcp_stdio_launcher.ps1` with `../temp/genko_mcp.env`.

### Smoke test

```powershell
python scripts/smoke_test.py --credentials ../temp/multi_merchant_credentials.json `
  --merchant-url https://lithe-production.up.railway.app
```

Expect **12** tools on `tools/list`.

**Agent boundary:** user agents connect only to `POST /mcp` on the platform. They
must not call merchant `/ucp/v1/*` directly. The vendor inbound key
(`UCP_GATEWAY_API_KEY` on the store) authorizes **platform → store** REST only.

> A separate `genko-backend` service on the `trusty-sleet` Railway project has a
> broken public domain; use **`genko-platform`** above.

## Health check

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Tests

```powershell
pytest
```

## Demo seed and smoke test

End-to-end validation against the python-sdk demo store (`python-sdk/examples/demo_store.py`) without touching production data unsafely.

### 1. Start the demo store (port 8100)

From the `python-sdk` directory:

```powershell
pip install -e ".[examples]"
uvicorn examples.demo_store:app --reload --port 8100
```

### 2. Start the backend (port 8000)

From this directory:

```powershell
uvicorn app.main:app --reload
```

### 3. Preview seed actions (no Supabase writes)

```powershell
python scripts/seed_demo.py --dry-run
```

### 4. Apply demo seed (requires Supabase service role in environment)

Ensure `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are set (for example via `.env`), then:

```powershell
python scripts/seed_demo.py --apply
```

Credentials are written once to `../temp/demo_seed_credentials.json` (gitignored). The script prints API key prefixes only on stdout.

### 5. Configure the demo store

Set these environment variables for `demo_store` from the seed output file:

- `UCP_PLATFORM_URL` — backend URL (for example `http://127.0.0.1:8000`)
- `UCP_PLATFORM_API_KEY` — `sdk_api_key` from the credentials file

### 6. Run the smoke test

```powershell
python scripts/smoke_test.py --credentials ../temp/demo_seed_credentials.json
```

The smoke test exercises health, MCP initialize/tools/list, catalog search, checkout flow, and optional payment authorization lookup. Required steps must pass; checkout completion and payment steps are skipped gracefully when preconditions are not met.
