"""Shared HTTP fetch with bounded retry on *transient* failures.

A build makes several load-bearing HTTP calls (city ArcGIS layers, GeoMet climate, ECCC
IDF, SoilGrids, the HRDEM STAC search). A single upstream blip — a 5xx, a 429, a dropped
connection, a timeout — used to kill the whole multi-minute build at whatever percent it
had reached. `request_with_retry` retries those with exponential backoff so a brief outage
self-heals; **permanent** failures (4xx) raise immediately, and exhausted retries re-raise
the same `requests` exception the callers already handle (so their graceful-degradation
paths — IDF→30 mm/h, soil→constant HSG, HRDEM→MRDEM — are unchanged).

Deliberately deterministic (no jitter): jitter matters for many-client thundering-herd on a
hosted deployment, tracked separately; here bounded backoff is enough and keeps tests fast.
"""
import time

import requests

# Statuses worth a retry: rate-limit + the standard transient 5xx gateway/overload family.
# A 4xx (bad request, not-found, unauthorized) is permanent — retrying only wastes minutes.
_TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    resp = getattr(exc, "response", None)
    return resp is not None and resp.status_code in _TRANSIENT_STATUS


def request_with_retry(method: str, url: str, *, retries: int = 2, backoff: float = 0.5,
                       sleep=time.sleep, **kwargs) -> requests.Response:
    """`requests.request(method, url, **kwargs)` with `raise_for_status`, retrying transient
    failures up to `retries` times with `backoff * 2**attempt` second waits. Returns the
    successful Response; raises on a permanent error or once retries are exhausted (the
    original `requests` exception). `sleep` is injectable so tests skip the real waits."""
    for attempt in range(retries + 1):
        try:
            resp = requests.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
            if attempt >= retries or not _is_transient(exc):
                raise
            sleep(backoff * 2 ** attempt)
