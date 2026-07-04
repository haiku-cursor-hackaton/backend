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
