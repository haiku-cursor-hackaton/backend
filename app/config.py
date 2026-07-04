from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=None,
        populate_by_name=True,
    )

    supabase_url: str = Field(validation_alias="SUPABASE_URL")
    supabase_service_role_key: str = Field(validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    public_base_url: str = Field(default="http://127.0.0.1:8000", validation_alias="PUBLIC_BASE_URL")
    mcp_path: str = Field(default="/mcp", validation_alias="MCP_PATH")
    environment: str = Field(default="development", validation_alias="ENVIRONMENT")


@lru_cache
def get_settings() -> Settings:
    return Settings()
