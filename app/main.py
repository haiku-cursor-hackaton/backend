import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app.api.router import api_router
from app.config import get_settings, parse_cors_origins
from app.mcp.server import build_mcp_router

logger = logging.getLogger(__name__)

app = FastAPI(title="Genko Backend")

_DEFAULT_CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]

try:
    _settings = get_settings()
    _mcp_path = _settings.mcp_path
    _extra_origins = parse_cors_origins(_settings.cors_origins)
    _cors_origins = _DEFAULT_CORS_ORIGINS + _extra_origins
except ValidationError:
    _mcp_path = "/mcp"
    _cors_origins = list(_DEFAULT_CORS_ORIGINS)

logger.info("CORS allow_origins: %s", _cors_origins)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)

app.include_router(build_mcp_router(path=_mcp_path))
