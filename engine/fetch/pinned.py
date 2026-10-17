"""Connection pinning: connect to the IP address that passed the address policy, never re-resolve.

The fetcher resolves a host once per hop, checks every address, and attaches the chosen address to
the request as extensions["pinned_ip"]. This transport rewrites the request URL to that address while
keeping the Host header and TLS SNI / certificate verification on the original hostname (httpcore's
"sni_hostname" extension), so a hostile DNS server cannot swap in a private address between check
and connect (DNS rebinding).

It fails closed: a request without a pinned address is refused. Keep-alive is disabled by the caller
(see Fetcher) so a TLS session negotiated for one hostname is never reused for another on the same IP.
"""

from __future__ import annotations

import httpx

from .netpolicy import is_public_ip


class PinningError(httpx.TransportError):
    pass


class PinnedTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | None = None):
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0, http2=False, trust_env=False,
                                                       limits=httpx.Limits(max_keepalive_connections=0))
        self.connections: list[tuple[str, str]] = []  # (hostname, ip) actually used; for audit and tests

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        ip = request.extensions.get("pinned_ip")
        if not ip:
            raise PinningError("refused: request has no pinned address (fail closed)", request=request)
        if not is_public_ip(ip):
            raise PinningError(f"refused: pinned address {ip} is not public", request=request)
        host = request.url.host
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")  # original host[:port]
        extensions = dict(request.extensions)
        if request.url.scheme == "https":
            extensions["sni_hostname"] = host  # TLS SNI and certificate checks use the real hostname
        pinned = httpx.Request(request.method, request.url.copy_with(host=ip), headers=headers,
                               stream=request.stream, extensions=extensions)
        self.connections.append((host, ip))
        response = await self.inner.handle_async_request(pinned)
        stream = response.extensions.get("network_stream")
        if stream is not None:  # real sockets: verify the peer is the address we checked
            peer = stream.get_extra_info("server_addr")
            if peer and peer[0] != ip:
                await response.aclose()
                raise PinningError(f"connected to {peer[0]}, expected pinned {ip}", request=request)
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()
