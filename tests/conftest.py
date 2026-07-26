import importlib

import dotenv
import pytest

ENV_VARS = [
    "RADARR_URL", "RADARR_API_KEY", "SONARR_URL", "SONARR_API_KEY",
    "LIDARR_URL", "LIDARR_API_KEY", "READARR_URL", "READARR_API_KEY",
    "QBITTORRENT_URL", "QBITTORRENT_USERNAME", "QBITTORRENT_PASSWORD",
    "IGNORE_TORRENT_TAGS", "STALLED_TIMEOUT", "STALLED_ACTION",
    "VERBOSE", "RUN_INTERVAL", "COUNT_DOWNLOADING_METADATA_AS_STALLED",
]


@pytest.fixture
def load_main(monkeypatch, tmp_path):
    """Reload main.py under a controlled environment and a temp CWD."""
    def _load(env=None):
        for var in ENV_VARS:
            monkeypatch.delenv(var, raising=False)
        for key, value in (env or {}).items():
            monkeypatch.setenv(key, value)
        # python-dotenv resolves .env from main.py's directory, so chdir cannot isolate it.
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
        monkeypatch.chdir(tmp_path)
        import main
        return importlib.reload(main)
    return _load


def queue_item(item_id=1, error="The download is stalled with no connections",
               download_client="qBittorrent", download_id="ABC123HASH",
               title="Some.Release.2024", **extra):
    return {"id": item_id, "errorMessage": error, "downloadClient": download_client,
            "downloadId": download_id, "title": title, **extra}


def queue_page(records, total=None):
    return {"records": records, "totalRecords": total if total is not None else len(records)}
