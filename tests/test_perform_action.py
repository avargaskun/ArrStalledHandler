import json
from urllib.parse import parse_qs, urlparse

import responses

BASE = "http://arr"
HEADERS = {"X-Api-Key": "k"}


def delete_query():
    call = [c for c in responses.calls if c.request.method == "DELETE"][0]
    return parse_qs(urlparse(call.request.url).query)


def posts():
    return [c for c in responses.calls if c.request.method == "POST"]


@responses.activate
def test_remove(load_main):
    m = load_main({"STALLED_ACTION": "REMOVE"})
    responses.delete(f"{BASE}/api/v3/queue/77", json={})

    m.perform_action(BASE, HEADERS, "77", None, "Radarr0", "v3")

    assert len(responses.calls) == 1
    assert delete_query() == {}


@responses.activate
def test_blocklist(load_main):
    m = load_main({"STALLED_ACTION": "BLOCKLIST"})
    responses.delete(f"{BASE}/api/v3/queue/77", json={})

    m.perform_action(BASE, HEADERS, "77", 770, "Radarr0", "v3")

    assert len(responses.calls) == 1
    assert delete_query() == {"blocklist": ["true"], "skipRedownload": ["true"]}


@responses.activate
def test_blocklist_and_search_radarr(load_main):
    m = load_main({"STALLED_ACTION": "BLOCKLIST_AND_SEARCH"})
    responses.delete(f"{BASE}/api/v3/queue/77", json={})
    responses.post(f"{BASE}/api/v3/command", json={})

    m.perform_action(BASE, HEADERS, "77", 770, "Radarr0", "v3")

    assert delete_query() == {"blocklist": ["true"], "skipRedownload": ["false"]}
    assert json.loads(posts()[0].request.body) == {"name": "MoviesSearch", "movieIds": [770]}


@responses.activate
def test_blocklist_and_search_sonarr(load_main):
    m = load_main({"STALLED_ACTION": "BLOCKLIST_AND_SEARCH"})
    responses.delete(f"{BASE}/api/v3/queue/77", json={})
    responses.post(f"{BASE}/api/v3/command", json={})

    m.perform_action(BASE, HEADERS, "77", None, "Sonarr1", "v3", episode_ids=[5, 6])

    assert json.loads(posts()[0].request.body) == {"name": "EpisodeSearch", "episodeIds": [5, 6]}


@responses.activate
def test_blocklist_and_search_lidarr_no_search(load_main):
    m = load_main({"STALLED_ACTION": "BLOCKLIST_AND_SEARCH"})
    responses.delete(f"{BASE}/api/v1/queue/77", json={})

    m.perform_action(BASE, HEADERS, "77", None, "lidarr0", "v1")

    assert len(responses.calls) == 1
    assert delete_query() == {"blocklist": ["true"], "skipRedownload": ["false"]}


@responses.activate
def test_blocklist_and_search_missing_ids_skips_search(load_main, caplog):
    m = load_main({"STALLED_ACTION": "BLOCKLIST_AND_SEARCH"})
    responses.delete(f"{BASE}/api/v3/queue/77", json={})

    m.perform_action(BASE, HEADERS, "77", None, "Radarr0", "v3")

    assert len(responses.calls) == 1
    assert "skipping search" in caplog.text


@responses.activate
def test_invalid_action_no_calls(load_main, caplog):
    m = load_main({"STALLED_ACTION": "EXPLODE"})

    m.perform_action(BASE, HEADERS, "77", 770, "Radarr0", "v3")

    assert len(responses.calls) == 0
    assert "Invalid STALLED_ACTION" in caplog.text


@responses.activate
def test_api_version_respected(load_main):
    m = load_main({"STALLED_ACTION": "BLOCKLIST"})
    responses.delete(f"{BASE}/api/v1/queue/77", json={})

    m.perform_action(BASE, HEADERS, "77", None, "readarr0", "v1")

    assert "/api/v1/queue/77" in responses.calls[0].request.url
