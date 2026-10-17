"""Network address policy: the single place that decides whether an address may be connected to.

Python's `ip.is_global` is necessary but not sufficient: IPv6 forms that embed an IPv4 address
(IPv4-mapped, NAT64, 6to4, Teredo) must be judged by the embedded address. For example
64:ff9b::7f00:1 (NAT64 for 127.0.0.1) reports is_global=True on Python 3.12.
"""

from __future__ import annotations

import ipaddress

NAT64_NETS = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def _embedded_ipv4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    out = []
    if ip.ipv4_mapped:
        out.append(ip.ipv4_mapped)
    if ip.sixtofour:
        out.append(ip.sixtofour)
    if ip.teredo:
        out.extend(ip.teredo)  # (server, client)
    if any(ip in net for net in NAT64_NETS):
        out.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return out


def is_public_ip(value: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    ip = ipaddress.ip_address(value) if isinstance(value, str) else value
    if not ip.is_global or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        return all(is_public_ip(v4) for v4 in _embedded_ipv4(ip))
    return True


def legacy_ipv4(host: str) -> ipaddress.IPv4Address | None:
    """Parse inet_aton-style numeric hosts (decimal, hex, octal, 1-3 part shorthand).

    Returns the address if `host` is numeric in any such form, else None. Resolvers differ on these
    (Windows rejects them, glibc accepts), so the engine decides itself.
    """
    parts = host.rstrip(".").split(".")
    if not 1 <= len(parts) <= 4 or any(p == "" for p in parts):
        return None
    vals = []
    for p in parts:
        low = p.lower()
        try:
            if low.startswith("0x"):
                vals.append(int(low[2:] or "0", 16))
            elif len(low) > 1 and low.startswith("0"):
                vals.append(int(low, 8))
            else:
                if not low.isdigit():
                    return None
                vals.append(int(low, 10))
        except ValueError:
            return None
    *head, last = vals
    if any(v > 255 for v in head) or last >= 256 ** (4 - len(head)):
        return None
    number = 0
    for v in head:
        number = number * 256 + v
    number = number * 256 ** (4 - len(head)) + last
    return ipaddress.IPv4Address(number)


def is_noncanonical_numeric(host: str) -> bool:
    """True for numeric hosts that are not a canonical dotted-quad (e.g. 2130706433, 0x7f.1, 0177.0.0.1)."""
    ip = legacy_ipv4(host)
    return ip is not None and str(ip) != host.rstrip(".")


def choose_ip(ips: list[str]) -> str:
    """Deterministic choice among resolved addresses: IPv4 first (broadest connectivity), then lowest."""
    return sorted(ips, key=lambda a: (ipaddress.ip_address(a).version, ipaddress.ip_address(a)))[0]
