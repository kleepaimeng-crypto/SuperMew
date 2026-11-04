"""Refresh Cookie 与认证请求同源边界，不提供跨源 Cookie 授权。"""

from urllib.parse import urlsplit

from starlette.datastructures import URL, Headers, MutableHeaders
from starlette.responses import JSONResponse, Response

from minimew.refresh import TokenPair
from minimew.settings import Settings

REFRESH_COOKIE = "minimew_refresh"


def set_refresh_cookie(response: Response, pair: TokenPair, settings: Settings) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        pair.refresh_token,
        max_age=settings.refresh_token_days * 86400,
        expires=pair.expires_at,
        path="/auth",
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="lax",
    )


def clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path="/auth",
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="lax",
    )


def origin(value: str) -> tuple[str, str, int] | None:
    try:
        if any(c.isspace() for c in value) or "\\" in value:
            return None
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
        ):
            return None
        return (
            parts.scheme,
            parts.hostname.lower(),
            parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80),
        )
    except ValueError:
        return None


class AuthHttpMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not (
            scope["path"] == "/auth" or scope["path"].startswith("/auth/")
        ):
            return await self.app(scope, receive, send)

        async def no_store(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["Pragma"] = "no-cache"
            await send(message)

        headers = Headers(scope=scope)
        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            source = headers.get("origin")
            if source is None:
                source = headers.get("referer")
            target = origin(str(URL(scope=scope)))
            fetch_site = headers.get("sec-fetch-site", "").lower()
            if (source is not None and (origin(source) is None or origin(source) != target)) or (
                source is None and fetch_site in {"same-site", "cross-site"}
            ):
                return await JSONResponse(
                    status_code=403, content={"detail": "Untrusted request origin"}
                )(scope, receive, no_store)
        return await self.app(scope, receive, no_store)
