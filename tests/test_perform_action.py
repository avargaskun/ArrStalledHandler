import json
import logging
from urllib.parse import parse_qs, urlparse

import pytest
import requests
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


def deletes():
    return [c for c in responses.calls if c.request.method == "DELETE"]


def errors(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


@pytest.mark.parametrize("disposition", DISPOSITIONS, ids=lambda d: d.name)
@responses.activate
def test_delete_params_match_disposition(load_main, disposition):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})
    responses.post(f"{BASE}/api/v3/command", json={})

    assert m.perform_action(make_app(), "77", 770, None, disposition) is True

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

    assert m.perform_action(make_app(), "77", 770, None, D.REMOVE_AND_BLOCKLIST_SEARCH) is True

    assert json.loads(posts()[0].request.body) == {"name": "MoviesSearch", "movieIds": [770]}


@responses.activate
def test_search_sonarr(load_main):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})
    responses.post(f"{BASE}/api/v3/command", json={})

    assert m.perform_action(
        make_app(type="sonarr", name="Sonarr1"), "77", None, [5, 6], D.KEEP_AND_BLOCKLIST_SEARCH
    ) is True

    assert json.loads(posts()[0].request.body) == {"name": "EpisodeSearch", "episodeIds": [5, 6]}


@responses.activate
def test_force_search_false_skips_post(load_main):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})

    assert m.perform_action(make_app(force_search=False), "77", 770, None, D.REMOVE_AND_BLOCKLIST_SEARCH) is True

    assert posts() == []
    assert len(responses.calls) == 1


@responses.activate
def test_non_search_disposition_skips_post(load_main):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={})

    assert m.perform_action(make_app(), "77", 770, None, D.REMOVE_AND_BLOCKLIST) is True

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


@responses.activate
def test_delete_404_is_benign_and_still_searches(load_main, caplog):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={}, status=404)
    responses.post(f"{BASE}/api/v3/command", json={})

    with caplog.at_level(logging.INFO):
        assert m.perform_action(make_app(), "77", 770, None, D.REMOVE_AND_BLOCKLIST_SEARCH) is True

    assert errors(caplog) == []
    assert any("already removed" in r.message for r in caplog.records)
    assert json.loads(posts()[0].request.body) == {"name": "MoviesSearch", "movieIds": [770]}


@responses.activate
def test_delete_500_returns_false_and_skips_search(load_main, caplog):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", json={}, status=500)
    responses.post(f"{BASE}/api/v3/command", json={})

    assert m.perform_action(make_app(), "77", 770, None, D.REMOVE_AND_BLOCKLIST_SEARCH) is False

    assert posts() == []
    records = errors(caplog)
    assert len(records) == 1
    assert "REMOVE_AND_BLOCKLIST_SEARCH" in records[0].message
    assert "will retry next cycle" in records[0].message
    assert "HTTP 500" in records[0].message


@responses.activate
def test_delete_connection_error_returns_false_and_skips_search(load_main, caplog):
    m = load_main()
    responses.delete(f"{BASE}/api/v3/queue/77", body=requests.exceptions.ConnectionError("boom"))
    responses.post(f"{BASE}/api/v3/command", json={})

    assert m.perform_action(make_app(), "77", 770, None, D.REMOVE_AND_BLOCKLIST_SEARCH) is False

    assert posts() == []
    assert any("will retry next cycle" in r.message for r in errors(caplog))


@responses.activate
def test_skip_delete_searches_without_deleting(load_main):
    m = load_main()
    responses.post(f"{BASE}/api/v3/command", json={})

    assert m.perform_action(
        make_app(type="sonarr", name="Sonarr1"), "77", None, [9], D.KEEP_AND_BLOCKLIST_SEARCH,
        skip_delete=True,
    ) is True

    assert deletes() == []
    assert json.loads(posts()[0].request.body) == {"name": "EpisodeSearch", "episodeIds": [9]}


@responses.activate
def test_skip_delete_without_force_search_makes_no_calls(load_main):
    m = load_main()

    assert m.perform_action(
        make_app(force_search=False), "77", 770, None, D.REMOVE_AND_BLOCKLIST_SEARCH, skip_delete=True
    ) is True

    assert len(responses.calls) == 0


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
