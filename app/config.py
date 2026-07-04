from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    supabase_url: str = Field(validation_alias=AliasChoices("SUPABASE_URL", "supabase_url"))
    supabase_service_role_key: str = Field(
        validation_alias=AliasChoices("SUPABASE_SERVICE_ROLE_KEY", "supabase_service_role_key")
    )
    public_base_url: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias=AliasChoices("PUBLIC_BASE_URL", "public_base_url"),
    )
    mcp_path: str = Field(default="/mcp", validation_alias=AliasChoices("MCP_PATH", "mcp_path"))
    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("ENVIRONMENT", "environment"),
    )
    demo_phone_number: str = Field(
        default="+10000000000",
        validation_alias=AliasChoices("DEMO_PHONE_NUMBER", "demo_phone_number"),
    )
    gateway_agent_name: str = Field(
        default="genko-gateway/0.1",
        validation_alias=AliasChoices("GATEWAY_AGENT_NAME", "gateway_agent_name"),
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
