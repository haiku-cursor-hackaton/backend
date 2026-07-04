from fastapi import Depends

from app.config import Settings, get_settings
from app.db.supabase import SupabaseClient


def get_supabase_client(settings: Settings = Depends(get_settings)) -> SupabaseClient:
    return SupabaseClient(
        supabase_url=settings.supabase_url,
        service_role_key=settings.supabase_service_role_key,
    )
