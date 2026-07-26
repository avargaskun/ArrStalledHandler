import threading
import time

import requests

BASE_URL = "http://127.0.0.1:9898"

_started = False


def _ensure_server(m):
    global _started
    if not _started:
        threading.Thread(target=m.start_health_server, daemon=True).start()
        _started = True
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            requests.get(f"{BASE_URL}/ping", timeout=1)
            return
        except requests.exceptions.RequestException:
            time.sleep(0.05)
    raise AssertionError("health server did not start within 5 seconds")


def test_ping_returns_200_ok(load_main):
    m = load_main({})
    _ensure_server(m)

    response = requests.get(f"{BASE_URL}/ping", timeout=1)

    assert response.status_code == 200
    assert response.text == "OK"


def test_other_path_404(load_main):
    m = load_main({})
    _ensure_server(m)

    response = requests.get(f"{BASE_URL}/anything-else", timeout=1)

    assert response.status_code == 404
