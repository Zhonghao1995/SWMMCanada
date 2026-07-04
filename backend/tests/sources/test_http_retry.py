"""request_with_retry: retry transient failures (5xx/429/connection/timeout) with backoff,
raise permanent failures (4xx) immediately, and re-raise the original exception once retries
are exhausted (so callers' graceful-degradation paths see the same error they always have)."""
import pytest
import requests

from swmmcanada.sources import _http


class _Resp:
    def __init__(self, status):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return {"status": self.status_code}


def _fake_requests(monkeypatch, outcomes):
    """Patch requests.request to yield each entry of `outcomes` in turn: an int -> a Response
    with that status; an Exception instance -> raised. Records how many calls happened."""
    calls = {"n": 0}
    seq = iter(outcomes)

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        item = next(seq)
        if isinstance(item, Exception):
            raise item
        return _Resp(item)

    monkeypatch.setattr(_http.requests, "request", fake_request)
    return calls


def _no_sleep(_seconds):
    pass


def test_success_first_try_no_retry(monkeypatch):
    calls = _fake_requests(monkeypatch, [200])
    resp = _http.request_with_retry("GET", "http://x", sleep=_no_sleep)
    assert resp.json() == {"status": 200} and calls["n"] == 1


def test_transient_5xx_then_success(monkeypatch):
    calls = _fake_requests(monkeypatch, [503, 200])
    resp = _http.request_with_retry("GET", "http://x", sleep=_no_sleep)
    assert resp.status_code == 200 and calls["n"] == 2


def test_connection_error_then_success(monkeypatch):
    calls = _fake_requests(monkeypatch, [requests.ConnectionError("reset"), 200])
    resp = _http.request_with_retry("GET", "http://x", sleep=_no_sleep)
    assert resp.status_code == 200 and calls["n"] == 2


def test_permanent_4xx_does_not_retry(monkeypatch):
    calls = _fake_requests(monkeypatch, [404, 200])
    with pytest.raises(requests.HTTPError):
        _http.request_with_retry("GET", "http://x", sleep=_no_sleep)
    assert calls["n"] == 1                              # no retry on a permanent error


def test_exhausted_retries_reraise(monkeypatch):
    calls = _fake_requests(monkeypatch, [500, 502, 503])
    with pytest.raises(requests.HTTPError):
        _http.request_with_retry("GET", "http://x", retries=2, sleep=_no_sleep)
    assert calls["n"] == 3                              # 1 initial + 2 retries, then re-raise


def test_backoff_grows_exponentially(monkeypatch):
    _fake_requests(monkeypatch, [500, 500, 200])
    waits = []
    _http.request_with_retry("GET", "http://x", retries=2, backoff=0.5, sleep=waits.append)
    assert waits == [0.5, 1.0]                          # 0.5 * 2**0, 0.5 * 2**1
