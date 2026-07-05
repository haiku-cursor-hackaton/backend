from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app.api.router import api_router
from app.config import get_settings
from app.mcp.server import build_mcp_router

app = FastAPI(title="Genko Backend")

try:
    _settings = get_settings()
    _mcp_path = _settings.mcp_path
    _cors_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]
except ValidationError:
    _mcp_path = "/mcp"
    _cors_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

app.include_router(build_mcp_router(path=_mcp_path))
