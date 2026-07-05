from __future__ import annotations

PYTHON_SDK_REPO = "https://github.com/haiku-cursor-hackaton/python-sdk.git"
AGENT_INTEGRATION_DOC_URL = (
    "https://raw.githubusercontent.com/haiku-cursor-hackaton/python-sdk/main/docs/AGENT_INTEGRATION.md"
)


def build_sdk_install_prompt(*, sdk_api_key: str, platform_url: str) -> str:
    base = platform_url.rstrip("/")
    return (
        "Genko SDK — coding agent prompt\n\n"
        "Paste into a coding agent (Claude Code, Codex, or Cursor):\n\n"
        "Ensure Python 3.10+ and FastAPI are in this project.\n\n"
        "Install Genko Skills:\n"
        f"git clone --depth 1 {PYTHON_SDK_REPO} .genko-sdk && "
        "mkdir -p .cursor/skills && "
        "cp -r .genko-sdk/.cursor/skills/wire-genko-sdk .cursor/skills/\n\n"
        f"Then read {AGENT_INTEGRATION_DOC_URL} and wire UCP into this store.\n\n"
        "# Credenciales Genko (.env del comercio)\n"
        f"UCP_PLATFORM_URL={base}\n"
        f"UCP_PLATFORM_API_KEY={sdk_api_key}\n"
    )
