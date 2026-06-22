"""Live ClimateHttpClient: the MSC GeoMet OGC API over HTTPS (requests)."""
import requests


class GeoMetClient:
    def __init__(self, timeout: float = 40.0):
        self.timeout = timeout

    def get_json(self, url: str, params: dict) -> dict:
        resp = requests.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
