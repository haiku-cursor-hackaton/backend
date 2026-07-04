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
