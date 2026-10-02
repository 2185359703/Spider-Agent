import time
from concurrent.futures import ThreadPoolExecutor

import requests

from auto_spider.services.request_interval import install_request_interval


def test_concurrent_requests_share_one_interval(monkeypatch):
    starts = []

    def fake_request(self, *args, **kwargs):
        starts.append(time.perf_counter())
        return "response"

    monkeypatch.setattr(requests.Session, "request", fake_request)
    restore = install_request_interval(0.025)
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert (
                list(
                    pool.map(lambda _: requests.Session().get("https://example.invalid"), range(4))
                )
                == ["response"] * 4
            )
        assert all(b - a >= 0.020 for a, b in zip(starts, starts[1:], strict=False))
    finally:
        restore()
