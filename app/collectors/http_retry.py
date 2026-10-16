"""HTTP helpers that retry transient DNS resolution failures.

The host's WiFi/DNS occasionally drops for a few seconds. A name-resolution
failure is safe to retry immediately; other errors are raised untouched so the
job-level retry (10 minutes later) still handles them.
"""

from __future__ import annotations

import logging
import time

import requests

logger = logging.getLogger(__name__)

DNS_RETRY_DELAYS = (2.0, 5.0)

_DNS_ERROR_MARKERS = (
    "nameresolutionerror",
    "name or service not known",
    "no address associated with hostname",
    "temporary failure in name resolution",
    "failed to resolve",
    "getaddrinfo failed",
)


def is_dns_error(exc: BaseException) -> bool:
    if not isinstance(exc, requests.ConnectionError):
        return False
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _DNS_ERROR_MARKERS)


def request_with_dns_retry(method: str, url: str, **kwargs) -> requests.Response:
    """``requests.<method>`` with up to ``len(DNS_RETRY_DELAYS)`` DNS-only retries."""
    send = getattr(requests, method.lower())
    for attempt, delay in enumerate(DNS_RETRY_DELAYS, start=1):
        try:
            return send(url, **kwargs)
        except requests.ConnectionError as exc:
            if not is_dns_error(exc):
                raise
            logger.warning(
                "dns resolution failed url=%s retry=%s/%s in %ss",
                url,
                attempt,
                len(DNS_RETRY_DELAYS),
                delay,
            )
            time.sleep(delay)
    return send(url, **kwargs)
