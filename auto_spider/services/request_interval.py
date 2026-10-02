def install_request_interval(seconds):
    """Apply one start-rate gate to supported HTTP clients in the isolated collector process."""
    import threading
    import time

    lock = threading.Lock()
    previous = [None]
    restored = []

    def gate():
        if previous[0] is not None:
            delay = seconds - (time.perf_counter() - previous[0])
            if delay > 0:
                time.sleep(delay)
        previous[0] = time.perf_counter()

    def wrap(owner):
        original = owner.request

        def request(self, *args, **kwargs):
            with lock:
                gate()
                return original(self, *args, **kwargs)

        owner.request = request
        restored.append((owner, original))

    import requests

    wrap(requests.Session)
    try:
        import httpx
    except ImportError:
        pass
    else:
        wrap(httpx.Client)
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        pass
    else:
        wrap(curl_requests.Session)

    def restore():
        for owner, original in restored:
            owner.request = original

    return restore
