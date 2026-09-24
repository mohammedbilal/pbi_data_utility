"""The one place a configured host becomes a URL.

``host_name`` in ``environments.json`` is stored bare — ``qa-trowe-pbi2.cddev.genesis.global``
— and https is assumed, which is correct for every QA box. It is *not* correct for a dev
server on plain http, and until 2026-09-24 six of the seven engines hardcoded
``f"https://{host}"``, so pointing the app at localhost meant editing Python in 13 places
and remembering to put it back. Hence this module: **an explicit scheme in ``host_name``
wins, and https is only the default.** A port survives normalization too, so
``http://localhost:8080`` works as a host_name.

Lifted verbatim from ``interest_engine``, which had it right from the start.
"""
from __future__ import annotations

from urllib.parse import urlparse


def normalize_host(host_name: str) -> str:
    """``host`` → ``https://host``; ``http://host:8080`` → unchanged.

    Raises ``ValueError`` on an empty or unparseable host, so a typo in Settings
    fails with a clear message instead of a confusing connection error.
    """
    host = (host_name or "").strip()
    if not host:
        raise ValueError("host_name is empty")
    if "://" not in host:
        host = f"https://{host}"
    parsed = urlparse(host)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid host_name: {host_name}")
    return f"{parsed.scheme}://{parsed.netloc}"


def join_url(host_name: str, path: str) -> str:
    """Join a normalized host to a path.

    The path is passed through **as written**: several engines post to ``/gwf//EVENT_X``
    with a doubled slash, which the server accepts and which is therefore not ours to
    "fix" here. Callers that want it collapsed already wrap this in their own
    ``_normalize_url``.
    """
    base = normalize_host(host_name)
    raw = (path or "").strip()
    if not raw:
        return base
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return f"{base}{raw}"
