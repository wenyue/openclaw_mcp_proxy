from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(slots=True)
class ProxyConfig:
    app_token: str
    openclaw_token: str
    session_ttl_seconds: int
    tool_timeout_seconds: int


def load_config() -> ProxyConfig:
    return ProxyConfig(
        app_token=os.getenv("OPENCLAW_PROXY_APP_TOKEN", ""),
        openclaw_token=os.getenv("OPENCLAW_PROXY_OPENCLAW_TOKEN", ""),
        session_ttl_seconds=int(os.getenv("OPENCLAW_PROXY_SESSION_TTL_SECONDS", "300")),
        tool_timeout_seconds=int(os.getenv("OPENCLAW_PROXY_TOOL_TIMEOUT_SECONDS", "120")),
    )
