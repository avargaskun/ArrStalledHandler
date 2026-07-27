import socket
import threading
import time

import pytest
import requests

from conftest import make_config


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(m):
    """Start a health server on a fresh port and return its base URL."""
    port = _free_port()
    threading.Thread(target=m.start_health_server, args=(port,), daemon=True).start()
    base_url = f"http://127.0.0.1:{port}"

    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            requests.get(f"{base_url}/ping", timeout=1)
            return base_url
        except requests.exceptions.RequestException:
            time.sleep(0.05)
    raise AssertionError("health server did not start within 5 seconds")


def test_ping_returns_200_ok(load_main):
    m = load_main({})
    base_url = _start_server(m)

    response = requests.get(f"{base_url}/ping", timeout=1)

    assert response.status_code == 200
    assert response.text == "OK"


def test_other_path_404(load_main):
    m = load_main({})
    base_url = _start_server(m)

    response = requests.get(f"{base_url}/anything-else", timeout=1)

    assert response.status_code == 404


def test_serves_on_configured_port(load_main):
    m = load_main({})
    first = _start_server(m)
    second = _start_server(m)

    assert first != second
    assert requests.get(f"{first}/ping", timeout=1).status_code == 200
    assert requests.get(f"{second}/ping", timeout=1).status_code == 200


def _run_main_once(m, monkeypatch, cfg):
    """Run main() with a stubbed config, exiting the loop on the first sleep."""
    started = []
    monkeypatch.setattr(m.config, "load_config", lambda *a, **k: cfg)
    monkeypatch.setattr(m.threading, "Thread", lambda **kwargs: started.append(kwargs) or _NoopThread())

    def _stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(m.time, "sleep", _stop)
    m.main()
    return started


class _NoopThread:
    def start(self):
        return None


def test_main_starts_health_thread_on_configured_port(load_main, monkeypatch):
    m = load_main({})
    cfg = make_config(arr_apps=(), health_enabled=True, health_port=12345)

    started = _run_main_once(m, monkeypatch, cfg)

    assert len(started) == 1
    assert started[0]["target"] is m.start_health_server
    assert started[0]["args"] == (12345,)
    assert started[0]["daemon"] is True


def test_main_skips_health_thread_when_disabled(load_main, monkeypatch):
    m = load_main({})
    cfg = make_config(arr_apps=(), health_enabled=False)

    started = _run_main_once(m, monkeypatch, cfg)

    assert started == []


def test_main_enables_debug_logging_when_verbose(load_main, monkeypatch):
    import logging

    m = load_main({})
    cfg = make_config(arr_apps=(), health_enabled=False, verbose=True)
    root = logging.getLogger()
    original_level = root.level
    try:
        _run_main_once(m, monkeypatch, cfg)
        assert root.level == logging.DEBUG
    finally:
        root.setLevel(original_level)


def test_main_runs_handlers_for_each_app(load_main, monkeypatch):
    m = load_main({})
    cfg = make_config(
        arr_apps=(m.config.ArrApp(type="radarr", name="Radarr0", url="http://arr", api_key="k"),),
        health_enabled=False,
        downloader=m.config.Downloader(name="default", url="http://qbit", username="u", password="p"),
    )
    calls = []
    monkeypatch.setattr(m, "handle_stalled_downloads", lambda *a: calls.append(("stalled", a)))
    monkeypatch.setattr(m, "detect_stuck_metadata_downloads", lambda *a: calls.append(("metadata", a)))

    _run_main_once(m, monkeypatch, cfg)

    assert [name for name, _ in calls] == ["stalled", "metadata"]
    qbit = calls[0][1][2]
    assert isinstance(qbit, m.QbitClient)
    assert (qbit.url, qbit.username, qbit.password) == ("http://qbit", "u", "p")


def test_main_logs_unexpected_errors(load_main, monkeypatch, caplog):
    m = load_main({})
    cfg = make_config(arr_apps=(make_config().arr_apps[0],), health_enabled=False)

    def _boom(*args):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(m, "handle_stalled_downloads", _boom)
    monkeypatch.setattr(m.config, "load_config", lambda *a, **k: cfg)
    with caplog.at_level("ERROR"):
        m.main()

    assert "kaboom" in caplog.text


def test_start_health_server_logs_failure(load_main, monkeypatch, caplog):
    m = load_main({})

    def _boom(*args, **kwargs):
        raise OSError("address in use")

    monkeypatch.setattr(m, "HTTPServer", _boom)
    with caplog.at_level("ERROR"):
        m.start_health_server(1234)

    assert "Failed to start health check server" in caplog.text
