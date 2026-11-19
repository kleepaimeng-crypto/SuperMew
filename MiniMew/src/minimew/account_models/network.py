"""公开HTTPS端点；解析后固定本次连接IP，TLS仍验证原服务名。"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx2


def public_url(value):
    from minimew.model_protocol import ModelConfiguration

    value = ModelConfiguration.credential_free_url(value)
    url = urlsplit(value)
    host = url.hostname or ""
    if url.scheme != "https" or url.port not in (None, 443) or not host or host.endswith("."):
        raise ValueError("Public HTTPS endpoint required")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or host.endswith((".localhost", ".local", ".internal")):
            raise ValueError("Public endpoint required") from None
    else:
        if not address.is_global:
            raise ValueError("Public endpoint required")
    return value


class PublicTransport(httpx2.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx2.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        public_url(str(request.url))
        host = request.url.host
        timeout = request.extensions.get("timeout", {}).get("connect", 10) or 10
        try:
            async with asyncio.timeout(timeout):
                records = await asyncio.to_thread(
                    socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM
                )
        except TimeoutError:
            raise httpx2.ConnectTimeout("Provider DNS timed out") from None
        except OSError:
            raise httpx2.ConnectError("Provider DNS failed") from None
        addresses = tuple(dict.fromkeys(row[4][0] for row in records))
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise httpx2.ConnectError("Provider endpoint rejected")
        pinned = httpx2.Request(
            request.method,
            request.url.copy_with(host=addresses[0]),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": host},
        )
        return await self.inner.handle_async_request(pinned)

    async def aclose(self):
        await self.inner.aclose()
