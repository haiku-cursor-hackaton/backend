from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    supabase_url: str = Field(validation_alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    public_base_url: str = Field(default="http://127.0.0.1:8000", validation_alias="PUBLIC_BASE_URL")
    mcp_path: str = Field(default="/mcp", validation_alias="MCP_PATH")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")
    demo_phone_number: str = Field(default="+10000000000", validation_alias="DEMO_PHONE_NUMBER")
    gateway_agent_name: str = Field(default="genko-gateway/0.1", validation_alias="GATEWAY_AGENT_NAME")


@lru_cache
def get_settings() -> Settings:
    return Settings()
