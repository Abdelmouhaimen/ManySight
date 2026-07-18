"""Resolve StoreLens public endpoints from one editable JSON registry.

Deployment environment variables override the selected profile. Skills, discovery,
the dashboard, and hosted processes all consume the same resolved values.
"""
import json
import os


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.environ.get(
    "STORELENS_ENDPOINT_CONFIG",
    os.path.join(ROOT, "config", "endpoints.json"),
)


def _load() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def _join(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}" if path != "/" else base.rstrip("/") + "/"


def resolve(request_base: str | None = None) -> dict:
    config = _load()
    profile_name = os.environ.get("STORELENS_ENDPOINT_PROFILE", config.get("active_profile", "local"))
    profiles = config.get("profiles", {})
    if profile_name not in profiles:
        raise RuntimeError(f"unknown StoreLens endpoint profile '{profile_name}'")
    profile = profiles[profile_name]
    public_url = (
        os.environ.get("STORELENS_PUBLIC_URL")
        or profile.get("public_url")
        or request_base
        or "http://localhost:8000"
    ).rstrip("/")
    paths = config.get("paths", {})
    mcp_url = (
        os.environ.get("STORELENS_PUBLIC_MCP_URL")
        or profile.get("mcp_url")
        or _join(public_url, paths.get("mcp", "/mcp"))
    ).rstrip("/")
    configured_cors = profile.get("cors_origins", [])
    cors_override = os.environ.get("STORELENS_CORS_ORIGINS")
    cors_origins = (
        [value.strip() for value in cors_override.split(",") if value.strip()]
        if cors_override is not None
        else configured_cors
    )
    result = {
        "profile": profile_name,
        "config_path": CONFIG_PATH,
        "public_url": public_url,
        "dashboard_url": _join(public_url, paths.get("dashboard", "/")),
        "mcp_url": mcp_url,
        "cors_origins": cors_origins,
        "paths": paths,
    }
    for key, path in paths.items():
        if key not in {"dashboard", "mcp"}:
            result[f"{key}_url"] = _join(public_url, path)
    return result
