"""Live ClimateHttpClient: the MSC GeoMet OGC API over HTTPS (requests)."""
from swmmcanada.sources import _http


class GeoMetClient:
    def __init__(self, timeout: float = 40.0):
        self.timeout = timeout

    def get_json(self, url: str, params: dict) -> dict:
        return _http.request_with_retry("GET", url, params=params, timeout=self.timeout).json()
