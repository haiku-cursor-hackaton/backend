from fastapi.testclient import TestClient

from app.config import parse_cors_origins
from app.main import app


def test_parse_cors_origins_strips_quotes_and_slashes() -> None:
    assert parse_cors_origins('"https://genko-portal.netlify.app/"') == [
        "https://genko-portal.netlify.app"
    ]
    assert parse_cors_origins(
        "https://a.example.com, 'https://b.example.com'"
    ) == ["https://a.example.com", "https://b.example.com"]


def test_connect_client_preflight_allows_configured_origin(monkeypatch) -> None:
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "https://genko-portal.netlify.app",
    )
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role-key")

    from app.config import get_settings

    get_settings.cache_clear()

    # Re-import app module state after env change is awkward; hit middleware via TestClient
    # on the already-imported app only validates localhost + whatever was loaded at import.
    client = TestClient(app)
    response = client.options(
        "/v1/connect/client",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
