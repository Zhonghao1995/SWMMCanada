"""Shared file-download helper for cities that publish static ZIP/SHP dumps instead of a
query API (Windsor's DBF zips, North Vancouver District's SHP zips).

One download per process host: files land in a shared cache directory keyed by name, and
an existing non-empty file is reused. The publishers refresh these dumps on month-scale
cadences, so per-process caching is honest; delete the cache dir to force a refresh.
"""
import hashlib
import tempfile
from pathlib import Path

from swmmcanada.sources import _http


def fetch_file(url: str, *, cache_name: str = None, timeout: float = 300.0) -> Path:
    """Download ``url`` into the shared cache once; return the local path."""
    cache = Path(tempfile.gettempdir()) / "swmmcanada_downloads"
    cache.mkdir(exist_ok=True)
    name = cache_name or (hashlib.sha1(url.encode()).hexdigest()[:16] + Path(url).suffix)
    path = cache / name
    if not path.exists() or path.stat().st_size == 0:
        resp = _http.request_with_retry("GET", url, timeout=timeout)
        path.write_bytes(resp.content)
    return path
