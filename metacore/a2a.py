"""A2A hardening helpers: card resolution (0.3->1.0 promotion, SSRF allowlist) and
fail-fast check that the reliability extension was really activated."""
from __future__ import annotations
import ipaddress, json, socket
from urllib.parse import urlparse
from urllib.request import urlopen

EXT_URI = "https://google.github.io/adk-docs/a2a/a2a-extension/"
WELL_KNOWN = "/.well-known/agent-card.json"


class A2AError(Exception):
    pass


def promote_card(card: dict) -> dict:
    """Legacy 0.3 (url + preferredTransport) -> 1.0 (supportedInterfaces[])."""
    card = dict(card)
    if "supportedInterfaces" not in card:
        if "url" not in card:
            raise A2AError("card has neither supportedInterfaces nor url")
        card["supportedInterfaces"] = [{"protocolBinding": card.get("preferredTransport", "JSONRPC"),
                                        "url": card["url"]}]
    return card


def check_url(url: str, allowed_hosts: set[str]) -> None:
    u = urlparse(url)
    if u.scheme not in ("https", "http") or not u.hostname:
        raise A2AError(f"bad url: {url}")
    if u.hostname not in allowed_hosts:
        raise A2AError(f"host not in allowlist: {u.hostname}")
    try:
        ip = ipaddress.ip_address(socket.gethostbyname(u.hostname))
    except OSError:
        return  # unresolved: nothing to block; connect will fail later
    if (ip.is_link_local or ip.is_private or ip.is_loopback) and u.hostname not in allowed_hosts:
        raise A2AError(f"internal address blocked: {ip}")
    if str(ip).startswith("169.254."):
        raise A2AError("cloud metadata address blocked")


def fetch_card(base_url: str, allowed_hosts: set[str], timeout: float = 5) -> dict:
    check_url(base_url, allowed_hosts)
    with urlopen(base_url.rstrip("/") + WELL_KNOWN, timeout=timeout) as r:  # noqa: S310
        card = promote_card(json.load(r))
    for i in card["supportedInterfaces"]:
        check_url(i["url"], allowed_hosts)
    return card


def request_headers(use_legacy: bool = False) -> dict:
    return {} if use_legacy else {"X-A2A-Extensions": EXT_URI}


def require_extension_confirmed(response_metadata: dict, use_legacy: bool = False) -> None:
    """Fail fast on silent fallback (proxy stripped the header)."""
    if use_legacy:
        return
    if EXT_URI not in (response_metadata or {}).get("activated_extensions", []):
        raise A2AError("reliability extension requested but not confirmed; refusing legacy fallback")
