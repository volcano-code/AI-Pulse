"""Fixed-source HTTPS fetcher. Production must also restrict network egress."""
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit
import httpx
from .config import Settings

class FetchError(RuntimeError):
    """Safe source-fetch failure with a machine-readable, non-secret reason code."""
    def __init__(self, message: str, code: str = "fetch_error"):
        super().__init__(message)
        self.code = code

def public_https(url: str, allowed_hosts: set[str], resolver=socket.getaddrinfo):
    p = urlsplit(url)
    if p.scheme != "https" or p.hostname not in allowed_hosts or p.username or p.password or p.port not in {None, 443}:
        raise FetchError("URL is outside the configured HTTPS allowlist", "policy_blocked")
    addresses = resolver(p.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise FetchError("Private or non-global destination blocked", "private_destination")

class SafeFetcher:
    def __init__(self, settings: Settings, transport=None, resolver=socket.getaddrinfo):
        self.settings = settings
        self.transport = transport
        self.resolver = resolver

    def fetch(self, url: str) -> bytes:
        with httpx.Client(timeout=self.settings.fetch_timeout_seconds, follow_redirects=False,
                          trust_env=False, transport=self.transport,
                          headers={"User-Agent": self.settings.fetch_user_agent,
                                   "Accept": "application/atom+xml,application/rss+xml,application/json,application/xml"}) as client:
            for _ in range(4):
                public_https(url, self.settings.fetch_hosts, self.resolver)
                with client.stream("GET", url) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise FetchError("Redirect has no destination", "invalid_redirect")
                        url = urljoin(url, location)
                        continue
                    response.raise_for_status()
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.settings.max_fetch_bytes:
                            raise FetchError("Response exceeds byte budget", "response_too_large")
                        chunks.append(chunk)
                    return b"".join(chunks)
        raise FetchError("Redirect budget exceeded", "redirect_budget_exceeded")
