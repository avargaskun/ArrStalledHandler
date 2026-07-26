import importlib

import dotenv
import pytest

import config

ENV_VARS = [
    "RADARR_URL", "RADARR_API_KEY", "SONARR_URL", "SONARR_API_KEY",
    "LIDARR_URL", "LIDARR_API_KEY", "READARR_URL", "READARR_API_KEY",
    "QBITTORRENT_URL", "QBITTORRENT_USERNAME", "QBITTORRENT_PASSWORD",
    "IGNORE_TORRENT_TAGS", "STALLED_TIMEOUT", "STALLED_ACTION",
    "VERBOSE", "RUN_INTERVAL", "COUNT_DOWNLOADING_METADATA_AS_STALLED",
    "CONFIG_FILE",
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


def make_watcher(name="default", tags=(), stalled_timeout=3600,
                 action=config.QueueItemDisposition.REMOVE_AND_BLOCKLIST_SEARCH):
    return config.Watcher(name=name, tags=tuple(tags), stalled_timeout=stalled_timeout, action=action)


def make_app(type="radarr", name="Radarr0", url="http://arr", api_key="k", force_search=True):
    return config.ArrApp(type=type, name=name, url=url, api_key=api_key, force_search=force_search)


def make_config(**overrides):
    """Build an AppConfig for handler tests; every field can be overridden by keyword."""
    defaults = {
        "run_interval": 300,
        "verbose": False,
        "health_enabled": True,
        "health_port": 9898,
        "db_file": "stalled_downloads.db",
        "count_metadata_as_stalled": False,
        "arr_apps": (make_app(),),
        "downloader": None,
        "watchers": (make_watcher(),),
    }
    defaults.update(overrides)
    return config.AppConfig(**defaults)
