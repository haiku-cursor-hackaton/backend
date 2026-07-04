from fastapi import FastAPI
from pydantic import ValidationError

from app.api.router import api_router
from app.config import get_settings
from app.mcp.server import build_mcp_router

app = FastAPI(title="Genko Backend")
app.include_router(api_router)

try:
    _mcp_path = get_settings().mcp_path
except ValidationError:
    _mcp_path = "/mcp"

app.include_router(build_mcp_router(path=_mcp_path))
