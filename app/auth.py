from __future__ import annotations

from fastapi import Header, HTTPException, status
from fastapi.websockets import WebSocket


def _extract_bearer_token(raw_authorization: str | None) -> str | None:
    if not raw_authorization:
        return None
    if not raw_authorization.lower().startswith("bearer "):
        return None
    return raw_authorization[7:].strip()


def require_bearer_token(
    authorization: str | None,
    expected_token: str,
    *,
    realm: str,
) -> None:
    if not expected_token:
        return
    actual = _extract_bearer_token(authorization)
    if actual != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid bearer token for {realm}.",
        )


def require_app_token(authorization: str | None = Header(default=None), expected_token: str = "") -> None:
    require_bearer_token(authorization, expected_token, realm="app")


def require_openclaw_token(
    authorization: str | None = Header(default=None),
    expected_token: str = "",
) -> None:
    require_bearer_token(authorization, expected_token, realm="openclaw")


async def require_websocket_app_token(
    websocket: WebSocket,
    expected_token: str,
) -> None:
    if not expected_token:
        return
    actual = _extract_bearer_token(websocket.headers.get("authorization"))
    if actual != expected_token:
        await websocket.close(code=4401, reason="Invalid app token.")
        raise RuntimeError("Invalid app token.")
