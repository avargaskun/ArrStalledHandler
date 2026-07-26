import json
from urllib.parse import parse_qs, urlparse

import pytest
import responses

import config
from config import QueueItemDisposition as D

BASE = "http://arr"
DISPOSITIONS = [d for d in D if d is not D.IGNORE]


def make_app(type="radarr", name="Radarr0", url=BASE, api_key="k", force_search=True):
    return config.ArrApp(type=type, name=name, url=url, api_key=api_key, force_search=force_search)


def delete_query():
    call = [c for c in responses.calls if c.request.method == "DELETE"][0]
    return parse_qs(urlparse(call.request.url).query)


def posts():
    return [c for c in responses.calls if c.request.method == "POST"]


@pytest.mark.parametrize("disposition", DISPOSITIONS, ids=lambda d: d.name)
@responses.activate
def test_delete_params_match_disposition(load_main, disposition):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})
    responses.post(f"{BASE}/api/v3/command", json={})

    m.perform_action(make_app(), "77", 770, None, disposition)

    assert delete_query() == {k: [v] for k, v in disposition.as_params().items()}


@responses.activate
def test_ignore_raises(load_main):
    m = load_main()

    with pytest.raises(ValueError):
        m.perform_action(make_app(), "77", 770, None, D.IGNORE)

    assert len(responses.calls) == 0


@responses.activate
def test_api_key_header_from_app(load_main):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})

    m.perform_action(make_app(api_key="secret"), "77", None, None, D.REMOVE)

    assert responses.calls[0].request.headers["X-Api-Key"] == "secret"


@responses.activate
def test_search_radarr(load_main):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})
    responses.post(f"{BASE}/api/v3/command", json={})

    m.perform_action(make_app(), "77", 770, None, D.REMOVE_AND_BLOCKLIST_SEARCH)

    assert json.loads(posts()[0].request.body) == {"name": "MoviesSearch", "movieIds": [770]}


@responses.activate
def test_search_sonarr(load_main):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})
    responses.post(f"{BASE}/api/v3/command", json={})

    m.perform_action(make_app(type="sonarr", name="Sonarr1"), "77", None, [5, 6], D.KEEP_AND_BLOCKLIST_SEARCH)

    assert json.loads(posts()[0].request.body) == {"name": "EpisodeSearch", "episodeIds": [5, 6]}


@responses.activate
def test_force_search_false_skips_post(load_main):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})

    m.perform_action(make_app(force_search=False), "77", 770, None, D.REMOVE_AND_BLOCKLIST_SEARCH)

    assert posts() == []
    assert len(responses.calls) == 1


@responses.activate
def test_non_search_disposition_skips_post(load_main):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})

    m.perform_action(make_app(), "77", 770, None, D.REMOVE_AND_BLOCKLIST)

    assert posts() == []


@responses.activate
def test_lidarr_search_skipped(load_main):
    m = load_main()
    responses.delete(f"{BASE}/api/v1/queue/77", json={})

    m.perform_action(make_app(type="lidarr", name="Lidarr0"), "77", None, None, D.REMOVE_AND_BLOCKLIST_SEARCH)

    assert posts() == []
    assert len(responses.calls) == 1
    assert "/api/v1/queue/77" in responses.calls[0].request.url


@responses.activate
def test_missing_ids_warns_and_skips_search(load_main, caplog):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})

    m.perform_action(make_app(), "77", None, None, D.REMOVE_AND_BLOCKLIST_SEARCH)

    assert posts() == []
    assert "skipping search" in caplog.text


@pytest.mark.parametrize(
    "legacy, expected, search",
    [
        ("REMOVE", {"removeFromClient": ["true"], "changeCategory": ["false"],
                    "blocklist": ["false"], "skipRedownload": ["false"]}, False),
        ("BLOCKLIST", {"removeFromClient": ["true"], "changeCategory": ["false"],
                       "blocklist": ["true"], "skipRedownload": ["true"]}, False),
        ("BLOCKLIST_AND_SEARCH", {"removeFromClient": ["true"], "changeCategory": ["false"],
                                  "blocklist": ["true"], "skipRedownload": ["false"]}, True),
    ],
)
@responses.activate
def test_legacy_action_parity(load_main, legacy, expected, search):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})
    responses.post(f"{BASE}/api/v3/command", json={})

    m.perform_action(make_app(), "77", 770, None, D.parse(legacy))

    assert delete_query() == expected
    assert bool(posts()) is search
