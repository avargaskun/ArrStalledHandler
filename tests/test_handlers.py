import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import responses

from conftest import queue_item, queue_page

RADARR = "http://radarr:7878"
QUEUE_URL = f"{RADARR}/api/v3/queue"
ARR_ENV = {"RADARR_URL": RADARR, "RADARR_API_KEY": "key"}
QBIT = "http://qbit:8080"
METADATA_ERROR = "qBittorrent is Downloading Metadata"


def calls(method):
    return [c for c in responses.calls if c.request.method == method]


def query(method="GET", index=0):
    return parse_qs(urlparse(calls(method)[index].request.url).query)


def ago(seconds):
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


@responses.activate
def test_new_stalled_download_recorded_not_acted(load_main):
    m = load_main({**ARR_ENV, "STALLED_TIMEOUT": "3600", "STALLED_ACTION": "BLOCKLIST"})
    m.initialize_database()
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    m.handle_stalled_downloads(RADARR, "key", "Radarr0", "v3")

    assert list(m.get_stalled_downloads_from_db("Radarr0")) == ["1"]
    assert len(responses.calls) == 1


@responses.activate
def test_within_timeout_not_acted(load_main):
    m = load_main({**ARR_ENV, "STALLED_TIMEOUT": "3600", "STALLED_ACTION": "BLOCKLIST"})
    m.initialize_database()
    first_detected = ago(100)
    m.add_stalled_download_to_db("1", first_detected, "Radarr0")
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    m.handle_stalled_downloads(RADARR, "key", "Radarr0", "v3")

    assert calls("DELETE") == []
    assert m.get_stalled_downloads_from_db("Radarr0") == {"1": first_detected}


@responses.activate
def test_past_timeout_acted_and_row_removed(load_main):
    m = load_main({**ARR_ENV, "STALLED_TIMEOUT": "3600", "STALLED_ACTION": "BLOCKLIST"})
    m.initialize_database()
    m.add_stalled_download_to_db("1", ago(3700), "Radarr0")
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(RADARR, "key", "Radarr0", "v3")

    assert query("DELETE") == {"blocklist": ["true"], "skipRedownload": ["true"]}
    assert m.get_stalled_downloads_from_db("Radarr0") == {}


@responses.activate
def test_timeout_zero_acts_on_second_pass(load_main):
    m = load_main({**ARR_ENV, "STALLED_TIMEOUT": "0", "STALLED_ACTION": "BLOCKLIST"})
    m.initialize_database()
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(RADARR, "key", "Radarr0", "v3")
    assert calls("DELETE") == []

    m.handle_stalled_downloads(RADARR, "key", "Radarr0", "v3")

    assert len(calls("DELETE")) == 1
    assert m.get_stalled_downloads_from_db("Radarr0") == {}


@responses.activate
def test_wrong_error_message_ignored(load_main):
    m = load_main({**ARR_ENV, "STALLED_ACTION": "BLOCKLIST"})
    m.initialize_database()
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, error="Something else")]))

    m.handle_stalled_downloads(RADARR, "key", "Radarr0", "v3")

    assert m.get_stalled_downloads_from_db("Radarr0") == {}
    assert calls("DELETE") == []


@responses.activate
def test_queue_params_stalled(load_main):
    m = load_main(ARR_ENV)
    responses.get(QUEUE_URL, json=queue_page([]))

    m.handle_stalled_downloads(RADARR, "key", "Radarr0", "v3")
    m.handle_stalled_downloads(RADARR, "key", "Sonarr0", "v3")

    assert query()["protocol"] == ["torrent"]
    assert query()["status"] == ["warning"]
    assert query()["includeEpisode"] == ["false"]
    assert query(index=1)["includeEpisode"] == ["true"]


@responses.activate
def test_movie_id_extracted_for_radarr_instance(load_main):
    m = load_main({**ARR_ENV, "STALLED_TIMEOUT": "3600", "STALLED_ACTION": "BLOCKLIST_AND_SEARCH"})
    m.initialize_database()
    m.add_stalled_download_to_db("1", ago(3700), "Radarr0")
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, movieId=770)]))
    responses.delete(f"{QUEUE_URL}/1", json={})
    responses.post(f"{RADARR}/api/v3/command", json={})

    m.handle_stalled_downloads(RADARR, "key", "Radarr0", "v3")

    assert json.loads(calls("POST")[0].request.body) == {"name": "MoviesSearch", "movieIds": [770]}


@responses.activate
def test_ignored_tag_skips_tracking(load_main):
    m = load_main({**ARR_ENV, "STALLED_ACTION": "BLOCKLIST", "QBITTORRENT_URL": QBIT,
                   "QBITTORRENT_USERNAME": "admin", "QBITTORRENT_PASSWORD": "pw",
                   "IGNORE_TORRENT_TAGS": "slow"})
    m.initialize_database()
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.post(f"{QBIT}/api/v2/auth/login", body="Ok.")
    responses.get(f"{QBIT}/api/v2/torrents/properties", json={"name": "release"})
    responses.get(f"{QBIT}/api/v2/torrents/info", json=[{"tags": "slow"}])

    m.handle_stalled_downloads(RADARR, "key", "Radarr0", "v3")

    assert m.get_stalled_downloads_from_db("Radarr0") == {}
    assert calls("DELETE") == []


@responses.activate
def test_empty_queue_no_db_access(load_main):
    m = load_main(ARR_ENV)
    responses.get(QUEUE_URL, json=queue_page([]))

    m.handle_stalled_downloads(RADARR, "key", "Radarr0", "v3")

    assert len(responses.calls) == 1


@responses.activate
def test_metadata_disabled_no_api_call(load_main):
    m = load_main(ARR_ENV)

    m.detect_stuck_metadata_downloads(RADARR, "key", "Radarr0", "v3")

    assert len(responses.calls) == 0


@responses.activate
def test_metadata_enabled_full_flow(load_main):
    m = load_main({**ARR_ENV, "STALLED_TIMEOUT": "3600", "STALLED_ACTION": "BLOCKLIST",
                   "COUNT_DOWNLOADING_METADATA_AS_STALLED": "true"})
    m.initialize_database()
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, error=METADATA_ERROR)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.detect_stuck_metadata_downloads(RADARR, "key", "Radarr0", "v3")

    assert query()["status"] == ["queued"]
    assert list(m.get_stalled_downloads_from_db("Radarr0")) == ["1"]
    assert calls("DELETE") == []

    m.remove_stalled_download_from_db("1", "Radarr0")
    m.add_stalled_download_to_db("1", ago(3700), "Radarr0")
    m.detect_stuck_metadata_downloads(RADARR, "key", "Radarr0", "v3")

    assert query("DELETE") == {"blocklist": ["true"], "skipRedownload": ["true"]}
    assert m.get_stalled_downloads_from_db("Radarr0") == {}


@responses.activate
def test_metadata_and_stalled_share_db_namespace(load_main):
    m = load_main({**ARR_ENV, "STALLED_TIMEOUT": "0", "STALLED_ACTION": "BLOCKLIST",
                   "COUNT_DOWNLOADING_METADATA_AS_STALLED": "true"})
    m.initialize_database()
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, error=METADATA_ERROR)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(RADARR, "key", "Radarr0", "v3")
    assert list(m.get_stalled_downloads_from_db("Radarr0")) == ["1"]

    m.detect_stuck_metadata_downloads(RADARR, "key", "Radarr0", "v3")

    assert len(calls("DELETE")) == 1
    assert m.get_stalled_downloads_from_db("Radarr0") == {}
