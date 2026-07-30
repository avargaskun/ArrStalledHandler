import json
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import responses

import config
from config import QueueItemDisposition as D
from conftest import make_app, make_config, make_watcher, queue_item, queue_page

RADARR = "http://radarr:7878"
QUEUE_URL = f"{RADARR}/api/v3/queue"
DOWNLOAD_CLIENT_URL = f"{RADARR}/api/v3/downloadclient"
QBIT_CLIENT = [{"id": 1, "name": "qBittorrent", "implementation": "QBittorrent"}]
QBIT = "http://qbit:8080"
LOGIN_URL = f"{QBIT}/api/v2/auth/login"
INFO_URL = f"{QBIT}/api/v2/torrents/info"
METADATA_ERROR = "qBittorrent is Downloading Metadata"

APP = make_app(url=RADARR, api_key="key")

BLOCKLIST_PARAMS = {"removeFromClient": ["true"], "changeCategory": ["false"],
                    "blocklist": ["true"], "skipRedownload": ["true"]}


def calls(method):
    return [c for c in responses.calls if c.request.method == method]


def query(method="GET", index=0):
    return parse_qs(urlparse(calls(method)[index].request.url).query)


def info_calls():
    return [c for c in responses.calls if c.request.url.startswith(INFO_URL)]


def ago(seconds):
    return datetime.now(timezone.utc) - timedelta(seconds=seconds)


def qbit_client(m, tags_pages, clients=None):
    """A QbitClient wired to mocked login + torrents/info responses (one page per lookup)."""
    responses.get(DOWNLOAD_CLIENT_URL, json=QBIT_CLIENT if clients is None else clients)
    responses.post(LOGIN_URL, body="Ok.")
    for tags in tags_pages:
        responses.get(INFO_URL, json=[{"tags": tags}] if tags is not None else [])
    return m.QbitClient(QBIT, "admin", "pw")


def tracked_config(m, cfg, download_id="1", first_detected=None, arr_service="Radarr0"):
    m.initialize_database(cfg.db_file)
    m.add_stalled_download_to_db(download_id, first_detected or ago(3700), arr_service,
                                 db_file=cfg.db_file)
    return cfg


@responses.activate
def test_new_stalled_download_recorded_not_acted(load_main):
    m = load_main()
    cfg = make_config(watchers=(make_watcher(action=D.REMOVE_AND_BLOCKLIST),))
    m.initialize_database(cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    m.handle_stalled_downloads(cfg, APP, None)

    assert list(m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file)) == ["1"]
    assert len(responses.calls) == 1


@responses.activate
def test_within_timeout_not_acted(load_main):
    m = load_main()
    cfg = make_config(watchers=(make_watcher(action=D.REMOVE_AND_BLOCKLIST),))
    first_detected = ago(100)
    tracked_config(m, cfg, first_detected=first_detected)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    m.handle_stalled_downloads(cfg, APP, None)

    assert calls("DELETE") == []
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": first_detected}


@responses.activate
def test_past_timeout_acted_and_row_removed(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(make_watcher(action=D.REMOVE_AND_BLOCKLIST),)))
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, None)

    assert query("DELETE") == BLOCKLIST_PARAMS
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}


@responses.activate
def test_timeout_zero_acts_on_second_pass(load_main):
    m = load_main()
    cfg = make_config(watchers=(make_watcher(stalled_timeout=0, action=D.REMOVE_AND_BLOCKLIST),))
    m.initialize_database(cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, None)
    assert calls("DELETE") == []

    m.handle_stalled_downloads(cfg, APP, None)

    assert len(calls("DELETE")) == 1
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}


@responses.activate
def test_wrong_error_message_ignored(load_main):
    m = load_main()
    cfg = make_config()
    m.initialize_database(cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, error="Something else")]))

    m.handle_stalled_downloads(cfg, APP, None)

    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}
    assert calls("DELETE") == []


@responses.activate
def test_queue_params_from_app_type(load_main):
    m = load_main()
    cfg = make_config()
    sonarr = make_app(type="sonarr", name="Sonarr0", url=RADARR, api_key="key")
    responses.get(QUEUE_URL, json=queue_page([]))

    m.handle_stalled_downloads(cfg, APP, None)
    m.handle_stalled_downloads(cfg, sonarr, None)

    assert query()["protocol"] == ["torrent"]
    assert query()["status"] == ["warning"]
    assert query()["includeEpisode"] == ["false"]
    assert query(index=1)["includeEpisode"] == ["true"]


@responses.activate
def test_movie_id_extracted_for_radarr_instance(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config())
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, movieId=770)]))
    responses.delete(f"{QUEUE_URL}/1", json={})
    responses.post(f"{RADARR}/api/v3/command", json={})

    m.handle_stalled_downloads(cfg, APP, None)

    assert json.loads(calls("POST")[0].request.body) == {"name": "MoviesSearch", "movieIds": [770]}


@responses.activate
def test_episode_ids_extracted_for_sonarr_instance(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(), arr_service="Sonarr0")
    sonarr = make_app(type="sonarr", name="Sonarr0", url=RADARR, api_key="key")
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, episodeId=42)]))
    responses.delete(f"{QUEUE_URL}/1", json={})
    responses.post(f"{RADARR}/api/v3/command", json={})

    m.handle_stalled_downloads(cfg, sonarr, None)

    assert json.loads(calls("POST")[0].request.body) == {"name": "EpisodeSearch", "episodeIds": [42]}


@responses.activate
def test_empty_queue_no_db_access(load_main):
    m = load_main()
    responses.get(QUEUE_URL, json=queue_page([]))

    m.handle_stalled_downloads(make_config(), APP, None)

    assert len(responses.calls) == 1


# --- watcher matching -------------------------------------------------------

@responses.activate
def test_first_matching_watcher_wins(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("first", tags=["slow"], stalled_timeout=0, action=D.REMOVE_AND_BLOCKLIST),
        make_watcher("second", tags=["slow"], stalled_timeout=0, action=D.KEEP),
        make_watcher("default", stalled_timeout=0, action=D.REMOVE),
    )))
    qbit = qbit_client(m, ["slow"])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert query("DELETE") == BLOCKLIST_PARAMS


@responses.activate
def test_tags_match_is_or_based(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["private", "keep"], action=D.KEEP),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, ["public, keep"])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert query("DELETE") == {"removeFromClient": ["false"], "changeCategory": ["false"],
                               "blocklist": ["false"], "skipRedownload": ["false"]}


@responses.activate
def test_tags_match_is_case_insensitive(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["SLOW"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, ["Slow"])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert calls("DELETE") == []


@responses.activate
def test_non_qbittorrent_item_falls_through_to_untagged_watcher(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, [])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, download_client="SABnzbd")]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert query("DELETE") == BLOCKLIST_PARAMS
    assert info_calls() == []


@responses.activate
def test_renamed_qbittorrent_client_still_matches_tags(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, ["slow"],
                       clients=[{"name": "Seedbox", "implementation": "QBittorrent"}])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, download_client="Seedbox")]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert calls("DELETE") == []


@responses.activate
def test_qbittorrent_named_client_of_another_implementation_is_not_matched(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, [],
                       clients=[{"name": "qBittorrent box", "implementation": "Transmission"}])
    responses.get(QUEUE_URL,
                  json=queue_page([queue_item(item_id=1, download_client="qBittorrent box")]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert query("DELETE") == BLOCKLIST_PARAMS
    assert info_calls() == []


@responses.activate
def test_download_client_lookup_failure_skips_item(load_main, caplog):
    m = load_main()
    first_detected = ago(3700)
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["keep"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )), first_detected=first_detected)
    responses.get(DOWNLOAD_CLIENT_URL, status=503)
    responses.post(LOGIN_URL, body="Ok.")
    qbit = m.QbitClient(QBIT, "admin", "pw")
    # A renamed client: a name-substring fallback would not recognise it and would let the
    # item fall through to the destructive catch-all, which is the whole point of skipping.
    responses.get(QUEUE_URL,
                  json=queue_page([queue_item(item_id=1, download_client="Seedbox")]))

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert calls("DELETE") == []
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": first_detected}
    assert any("skipped this cycle" in r.message for r in caplog.records)


@responses.activate
def test_download_client_lookup_failure_still_honours_an_untagged_first_watcher(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
        make_watcher("tagged", tags=["keep"], action=D.IGNORE),
    )))
    responses.get(DOWNLOAD_CLIENT_URL, status=503)
    responses.post(LOGIN_URL, body="Ok.")
    qbit = m.QbitClient(QBIT, "admin", "pw")
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert query("DELETE") == BLOCKLIST_PARAMS


@responses.activate
def test_nameless_client_does_not_match_a_nameless_queue_item(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["keep"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, [], clients=[{"name": None, "implementation": "QBittorrent"}])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, download_client=None)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert query("DELETE") == BLOCKLIST_PARAMS
    assert info_calls() == []


@responses.activate
def test_malformed_download_client_response_skips_item(load_main, caplog):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["keep"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    responses.get(DOWNLOAD_CLIENT_URL, json={"error": "not authorised"})
    responses.post(LOGIN_URL, body="Ok.")
    qbit = m.QbitClient(QBIT, "admin", "pw")
    responses.get(QUEUE_URL,
                  json=queue_page([queue_item(item_id=1, download_client="Seedbox")]))

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert calls("DELETE") == []
    assert any("skipped this cycle" in r.message for r in caplog.records)


@responses.activate
def test_no_qbittorrent_client_configured_warns(load_main, caplog):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["keep"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, [], clients=[{"name": "Deluge", "implementation": "Deluge"}])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert query("DELETE") == BLOCKLIST_PARAMS
    assert any("No qBittorrent download client" in r.message for r in caplog.records)


@responses.activate
def test_no_qbittorrent_client_warns_once_per_instance(load_main, caplog):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["keep"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    sonarr = make_app(type="sonarr", name="Sonarr0", url=RADARR, api_key="key")
    qbit = qbit_client(m, [], clients=[{"name": "Deluge", "implementation": "Deluge"}])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    for _ in range(2):
        m.handle_stalled_downloads(cfg, APP, qbit)
        m.handle_stalled_downloads(cfg, sonarr, qbit)

    warnings = [r.message for r in caplog.records if "No qBittorrent download client" in r.message]
    assert len(warnings) == 2
    assert sum("Radarr0" in w for w in warnings) == 1
    assert sum("Sonarr0" in w for w in warnings) == 1


@responses.activate
def test_no_qbittorrent_client_warns_again_after_recovering(load_main, caplog):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["keep"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    deluge = [{"name": "Deluge", "implementation": "Deluge"}]
    responses.get(DOWNLOAD_CLIENT_URL, json=deluge)
    responses.get(DOWNLOAD_CLIENT_URL, json=QBIT_CLIENT)
    responses.get(DOWNLOAD_CLIENT_URL, json=deluge)
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, json=[{"tags": ""}])
    qbit = m.QbitClient(QBIT, "admin", "pw")
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    for _ in range(3):
        m.handle_stalled_downloads(cfg, APP, qbit)

    warnings = [r for r in caplog.records if "No qBittorrent download client" in r.message]
    assert len(warnings) == 2


@responses.activate
def test_metadata_flow_matches_a_renamed_qbittorrent_client(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(count_metadata_as_stalled=True, watchers=(
        make_watcher("tagged", tags=["keep"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, ["keep"],
                       clients=[{"name": "Seedbox", "implementation": "QBittorrent"}])
    responses.get(QUEUE_URL, json=queue_page([queue_item(
        item_id=1, error=METADATA_ERROR, download_client="Seedbox")]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.detect_stuck_metadata_downloads(cfg, APP, qbit)

    assert calls("DELETE") == []
    assert len(info_calls()) == 1


@responses.activate
def test_download_clients_not_looked_up_without_tagged_watchers(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(make_watcher("default", action=D.REMOVE),)))
    qbit = qbit_client(m, [])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert [c for c in responses.calls if c.request.url.startswith(DOWNLOAD_CLIENT_URL)] == []


@responses.activate
def test_download_clients_looked_up_once_per_cycle(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )), download_id="1")
    m.add_stalled_download_to_db("2", ago(3700), "Radarr0", db_file=cfg.db_file)
    qbit = qbit_client(m, ["slow", "slow"])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1),
                                              queue_item(item_id=2, download_id="OTHERHASH")]))

    m.handle_stalled_downloads(cfg, APP, qbit)

    lookups = [c for c in responses.calls if c.request.url.startswith(DOWNLOAD_CLIENT_URL)]
    assert len(lookups) == 1
    assert len(info_calls()) == 2


@responses.activate
def test_no_downloader_falls_through_to_untagged_watcher(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, None)

    assert query("DELETE") == BLOCKLIST_PARAMS


@responses.activate
def test_tag_lookup_failure_skips_item(load_main, caplog):
    m = load_main()
    first_detected = ago(3700)
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )), first_detected=first_detected)
    responses.get(DOWNLOAD_CLIENT_URL, json=QBIT_CLIENT)
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, status=500)
    qbit = m.QbitClient(QBIT, "admin", "pw")
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert calls("DELETE") == []
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": first_detected}
    assert "Could not get torrent tags" in caplog.text


@responses.activate
def test_unknown_hash_falls_through_to_untagged_watcher(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, [None])  # qBittorrent returns [] for an unknown hash
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert query("DELETE") == BLOCKLIST_PARAMS


@responses.activate
def test_item_without_download_id_falls_through_to_untagged_watcher(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, [])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, download_id=None)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert query("DELETE") == BLOCKLIST_PARAMS
    assert info_calls() == []


@responses.activate
def test_null_download_client_falls_through_to_untagged_watcher(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, [])
    # A JSON null survives dict.get's default, so this used to raise AttributeError
    # and kill the whole poll loop.
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, download_client=None)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert query("DELETE") == BLOCKLIST_PARAMS
    assert info_calls() == []


@responses.activate
def test_null_error_message_is_skipped_without_crashing(load_main):
    m = load_main()
    cfg = make_config()
    m.initialize_database(cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, error=None)]))

    m.handle_stalled_downloads(cfg, APP, None)

    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}
    assert calls("DELETE") == []


@responses.activate
def test_tags_fetched_once_per_item(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("a", tags=["a"], action=D.KEEP),
        make_watcher("b", tags=["b"], action=D.KEEP),
        make_watcher("c", tags=["c"], action=D.KEEP),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, ["unrelated"])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert len(info_calls()) == 1
    assert query("DELETE") == BLOCKLIST_PARAMS


@responses.activate
def test_ignore_action_makes_no_call_and_no_db_insert(load_main):
    m = load_main()
    cfg = make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    ))
    m.initialize_database(cfg.db_file)
    qbit = qbit_client(m, ["slow"])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert calls("DELETE") == []
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}


@responses.activate
def test_ignore_action_leaves_existing_row_untouched(load_main):
    m = load_main()
    first_detected = ago(3700)
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )), first_detected=first_detected)
    qbit = qbit_client(m, ["slow"])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert calls("DELETE") == []
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": first_detected}


@responses.activate
def test_per_watcher_timeouts(load_main):
    m = load_main()
    cfg = make_config(watchers=(
        make_watcher("patient", tags=["private"], stalled_timeout=86400, action=D.REMOVE),
        make_watcher("impatient", tags=["public"], stalled_timeout=600, action=D.REMOVE_AND_BLOCKLIST),
    ))
    m.initialize_database(cfg.db_file)
    m.add_stalled_download_to_db("1", ago(3700), "Radarr0", db_file=cfg.db_file)
    m.add_stalled_download_to_db("2", ago(3700), "Radarr0", db_file=cfg.db_file)
    qbit = qbit_client(m, ["private", "public"])
    responses.get(QUEUE_URL, json=queue_page([
        queue_item(item_id=1, download_id="HASH1"),
        queue_item(item_id=2, download_id="HASH2"),
    ]))
    responses.delete(f"{QUEUE_URL}/2", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert len(calls("DELETE")) == 1
    assert "/queue/2" in calls("DELETE")[0].request.url
    assert query("DELETE") == BLOCKLIST_PARAMS
    assert list(m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file)) == ["1"]


def test_match_watcher_without_catch_all_skips_item(load_main):
    m = load_main()
    watchers = (make_watcher("tagged", tags=["slow"], action=D.REMOVE),)

    assert m.match_watcher(queue_item(), watchers, None, None) is m.SKIP_ITEM


# --- progress matching ------------------------------------------------------

KEEP_PARAMS = {"removeFromClient": ["false"], "changeCategory": ["false"],
               "blocklist": ["false"], "skipRedownload": ["false"]}

PROGRESS_WATCHERS = (
    make_watcher("nothing-downloaded", max_progress=0, action=D.REMOVE_AND_BLOCKLIST),
    make_watcher("default", action=D.KEEP),
)


@responses.activate
def test_max_progress_zero_matches_nothing_downloaded(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=PROGRESS_WATCHERS))
    responses.get(QUEUE_URL,
                  json=queue_page([queue_item(item_id=1, size=1000, sizeleft=1000)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, None)

    assert query("DELETE") == BLOCKLIST_PARAMS


@responses.activate
def test_max_progress_zero_rejects_any_bytes(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=PROGRESS_WATCHERS))
    responses.get(QUEUE_URL,
                  json=queue_page([queue_item(item_id=1, size=1000, sizeleft=999)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, None)

    assert query("DELETE") == KEEP_PARAMS


def test_min_progress_inclusive_boundary(load_main):
    m = load_main()
    watchers = (make_watcher("complete", min_progress=100, action=D.KEEP),
                make_watcher("started", min_progress=1, action=D.REMOVE),
                make_watcher("default", action=D.REMOVE_AND_BLOCKLIST))

    complete = queue_item(size=1000, sizeleft=0)
    untouched = queue_item(size=1000, sizeleft=1000)

    assert m.match_watcher(complete, watchers, None, None).name == "complete"
    assert m.match_watcher(untouched, watchers, None, None).name == "default"


def test_progress_range_min_and_max(load_main):
    m = load_main()
    watchers = (make_watcher("middle", min_progress=25, max_progress=75, action=D.KEEP),
                make_watcher("default", action=D.REMOVE_AND_BLOCKLIST))

    def matched(sizeleft):
        return m.match_watcher(queue_item(size=1000, sizeleft=sizeleft),
                               watchers, None, None).name

    assert matched(500) == "middle"
    assert matched(900) == "default"
    assert matched(100) == "default"


def test_zero_size_counts_as_zero_progress(load_main):
    m = load_main()
    watchers = (make_watcher("nothing", max_progress=0, action=D.KEEP),
                make_watcher("default", action=D.REMOVE_AND_BLOCKLIST))

    matched = m.match_watcher(queue_item(size=0, sizeleft=0), watchers, None, None)

    assert matched.name == "nothing"


def test_inconsistent_sizes_clamped(load_main):
    m = load_main()
    watchers = (make_watcher("nothing", max_progress=0, action=D.KEEP),
                make_watcher("complete", min_progress=100, action=D.REMOVE),
                make_watcher("default", action=D.REMOVE_AND_BLOCKLIST))

    over = queue_item(size=1000, sizeleft=1500)
    negative = queue_item(size=1000, sizeleft=-1)

    assert m.match_watcher(over, watchers, None, None).name == "nothing"
    assert m.match_watcher(negative, watchers, None, None).name == "complete"


def test_boolean_sizes_are_unreadable(load_main):
    m = load_main()

    assert m._item_progress(queue_item(size=True, sizeleft=True)) is None
    assert m._item_progress(queue_item(size=1000, sizeleft=False)) is None


@responses.activate
def test_unreadable_progress_skips_item(load_main, caplog):
    m = load_main()
    cfg = make_config(watchers=PROGRESS_WATCHERS)
    m.initialize_database(cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    m.handle_stalled_downloads(cfg, APP, None)

    assert calls("DELETE") == []
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}
    assert any("skipping it this cycle" in r.message for r in caplog.records)


@responses.activate
def test_unreadable_progress_irrelevant_without_progress_watchers(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, None)

    assert query("DELETE") == BLOCKLIST_PARAMS


@responses.activate
def test_progress_and_tags_are_anded(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("private-nothing", tags=["private"], max_progress=0, action=D.KEEP),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    for download_id in ("2", "3"):
        m.add_stalled_download_to_db(download_id, ago(3700), "Radarr0", db_file=cfg.db_file)
    qbit = qbit_client(m, ["public", "private"])
    responses.get(QUEUE_URL, json=queue_page([
        queue_item(item_id=1, download_id="TAGGEDHALF", size=1000, sizeleft=500),
        queue_item(item_id=2, download_id="UNTAGGEDZERO", size=1000, sizeleft=1000),
        queue_item(item_id=3, download_id="TAGGEDZERO", size=1000, sizeleft=1000),
    ]))
    for item_id in (1, 2, 3):
        responses.delete(f"{QUEUE_URL}/{item_id}", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert [c.request.url.split("?")[0] for c in calls("DELETE")] == [
        f"{QUEUE_URL}/1", f"{QUEUE_URL}/2", f"{QUEUE_URL}/3"]
    assert [query("DELETE", i) for i in range(3)] == [
        BLOCKLIST_PARAMS, BLOCKLIST_PARAMS, KEEP_PARAMS]
    assert len(info_calls()) == 2


@responses.activate
def test_progress_gate_skips_tag_lookup(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("private-nothing", tags=["private"], max_progress=0, action=D.KEEP),
        make_watcher("private-complete", tags=["private"], min_progress=100, action=D.KEEP),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    )))
    qbit = qbit_client(m, ["private"])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, size=1000, sizeleft=500)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, qbit)

    assert info_calls() == []
    assert query("DELETE") == BLOCKLIST_PARAMS


@responses.activate
def test_untagged_progress_watcher_needs_no_downloader(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(downloader=None, watchers=PROGRESS_WATCHERS))
    responses.get(QUEUE_URL,
                  json=queue_page([queue_item(item_id=1, size=1000, sizeleft=1000)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, None)

    assert query("DELETE") == BLOCKLIST_PARAMS
    assert [c for c in responses.calls if c.request.url.startswith(DOWNLOAD_CLIENT_URL)] == []


# --- metadata flow ----------------------------------------------------------

@responses.activate
def test_metadata_disabled_no_api_call(load_main):
    m = load_main()

    m.detect_stuck_metadata_downloads(make_config(count_metadata_as_stalled=False), APP, None)

    assert len(responses.calls) == 0


@responses.activate
def test_metadata_enabled_full_flow(load_main):
    m = load_main()
    cfg = make_config(count_metadata_as_stalled=True,
                      watchers=(make_watcher(action=D.REMOVE_AND_BLOCKLIST),))
    m.initialize_database(cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, error=METADATA_ERROR)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.detect_stuck_metadata_downloads(cfg, APP, None)

    assert query()["status"] == ["queued"]
    assert list(m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file)) == ["1"]
    assert calls("DELETE") == []

    m.remove_stalled_download_from_db("1", "Radarr0", db_file=cfg.db_file)
    m.add_stalled_download_to_db("1", ago(3700), "Radarr0", db_file=cfg.db_file)
    m.detect_stuck_metadata_downloads(cfg, APP, None)

    assert query("DELETE") == BLOCKLIST_PARAMS
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}


@responses.activate
def test_metadata_and_stalled_share_db_namespace(load_main):
    m = load_main()
    cfg = make_config(count_metadata_as_stalled=True,
                      watchers=(make_watcher(stalled_timeout=0, action=D.REMOVE_AND_BLOCKLIST),))
    m.initialize_database(cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, error=METADATA_ERROR)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.handle_stalled_downloads(cfg, APP, None)
    assert list(m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file)) == ["1"]

    m.detect_stuck_metadata_downloads(cfg, APP, None)

    assert len(calls("DELETE")) == 1
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}


@responses.activate
def test_each_flow_logs_its_own_wording(load_main, caplog):
    m = load_main()
    cfg = tracked_config(m, make_config(count_metadata_as_stalled=True,
                                        watchers=(make_watcher(action=D.REMOVE),)))
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, error=METADATA_ERROR)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    with caplog.at_level("INFO"):
        m.handle_stalled_downloads(cfg, APP, None)
        m.add_stalled_download_to_db("1", ago(3700), "Radarr0", db_file=cfg.db_file)
        m.detect_stuck_metadata_downloads(cfg, APP, None)

    assert "Handling stalled download ID 1" in caplog.text
    assert "Handling stuck metadata download ID 1" in caplog.text


@responses.activate
def test_metadata_flow_skips_null_error_message(load_main):
    m = load_main()
    cfg = make_config(count_metadata_as_stalled=True)
    m.initialize_database(cfg.db_file)
    # status=queued items routinely carry no error message at all.
    responses.get(QUEUE_URL, json=queue_page([
        queue_item(item_id=1, error=None),
        queue_item(item_id=2, error=METADATA_ERROR),
    ]))

    m.detect_stuck_metadata_downloads(cfg, APP, None)

    assert list(m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file)) == ["2"]


@responses.activate
def test_metadata_flow_empty_queue_no_db_access(load_main):
    m = load_main()
    responses.get(QUEUE_URL, json=queue_page([]))

    m.detect_stuck_metadata_downloads(make_config(count_metadata_as_stalled=True), APP, None)

    assert len(responses.calls) == 1


@responses.activate
def test_metadata_flow_uses_watcher_tags(load_main):
    m = load_main()
    cfg = make_config(count_metadata_as_stalled=True, watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
        make_watcher("default", action=D.REMOVE_AND_BLOCKLIST),
    ))
    m.initialize_database(cfg.db_file)
    qbit = qbit_client(m, ["slow"])
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, error=METADATA_ERROR)]))

    m.detect_stuck_metadata_downloads(cfg, APP, qbit)

    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}


@responses.activate
def test_metadata_flow_honours_progress_watchers(load_main):
    m = load_main()
    cfg = tracked_config(m, make_config(count_metadata_as_stalled=True,
                                        watchers=PROGRESS_WATCHERS))
    responses.get(QUEUE_URL, json=queue_page([
        queue_item(item_id=1, error=METADATA_ERROR, size=0, sizeleft=0)]))
    responses.delete(f"{QUEUE_URL}/1", json={})

    m.detect_stuck_metadata_downloads(cfg, APP, None)

    assert query("DELETE") == BLOCKLIST_PARAMS


# --- legacy env parity ------------------------------------------------------

@responses.activate
def test_legacy_env_config_end_to_end(load_main, tmp_path):
    m = load_main()
    cfg = config.load_config(str(tmp_path / "absent.yaml"), environ={
        "RADARR_URL": RADARR,
        "RADARR_API_KEY": "key",
        "STALLED_TIMEOUT": "3600",
        "STALLED_ACTION": "BLOCKLIST_AND_SEARCH",
    })
    app = cfg.arr_apps[0]
    m.initialize_database(cfg.db_file)
    m.add_stalled_download_to_db("1", ago(3700), app.name, db_file=cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, movieId=770)]))
    responses.delete(f"{QUEUE_URL}/1", json={})
    responses.post(f"{RADARR}/api/v3/command", json={})

    m.handle_stalled_downloads(cfg, app, None)

    assert app.name == "Radarr0"
    assert query("DELETE") == {"removeFromClient": ["true"], "changeCategory": ["false"],
                               "blocklist": ["true"], "skipRedownload": ["false"]}
    assert json.loads(calls("POST")[0].request.body) == {"name": "MoviesSearch", "movieIds": [770]}
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}


# --- handler return contracts -----------------------------------------------

@responses.activate
def test_handler_returns_seen_ids(load_main):
    m = load_main()
    cfg = make_config()
    m.initialize_database(cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1),
                                              queue_item(item_id=2, download_id="OTHERHASH")]))

    assert m.handle_stalled_downloads(cfg, APP, None) == {"1", "2"}


@responses.activate
def test_handler_returns_none_when_queue_read_fails(load_main, caplog):
    m = load_main()
    first_detected = ago(3700)
    cfg = tracked_config(m, make_config(), first_detected=first_detected)
    responses.get(QUEUE_URL, status=500)

    with caplog.at_level(logging.WARNING):
        assert m.handle_stalled_downloads(cfg, APP, None) is None

    assert "Could not read the queue of Radarr0" in caplog.text
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": first_detected}


@responses.activate
def test_handler_returns_empty_set_for_empty_queue(load_main):
    m = load_main()
    cfg = make_config()
    m.initialize_database(cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([]))

    seen = m.handle_stalled_downloads(cfg, APP, None)

    assert seen is not None
    assert seen == set()


@responses.activate
def test_handler_excludes_non_matching_error_messages(load_main):
    m = load_main()
    cfg = make_config()
    m.initialize_database(cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([
        queue_item(item_id=1),
        queue_item(item_id=2, error="Something else", download_id="OTHERHASH"),
    ]))

    assert m.handle_stalled_downloads(cfg, APP, None) == {"1"}


@responses.activate
def test_metadata_handler_returns_empty_set_when_disabled(load_main):
    m = load_main()

    seen = m.detect_stuck_metadata_downloads(make_config(count_metadata_as_stalled=False), APP, None)

    assert seen == set()
    assert len(responses.calls) == 0


@responses.activate
def test_metadata_handler_returns_seen_ids(load_main):
    m = load_main()
    cfg = make_config(count_metadata_as_stalled=True)
    m.initialize_database(cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=7, error=METADATA_ERROR)]))

    assert m.detect_stuck_metadata_downloads(cfg, APP, None) == {"7"}


@responses.activate
def test_metadata_handler_returns_none_when_queue_read_fails(load_main, caplog):
    m = load_main()
    first_detected = ago(3700)
    cfg = tracked_config(m, make_config(count_metadata_as_stalled=True),
                         first_detected=first_detected)
    responses.get(QUEUE_URL, status=500)

    with caplog.at_level(logging.WARNING):
        assert m.detect_stuck_metadata_downloads(cfg, APP, None) is None

    assert "Could not read the queue of Radarr0" in caplog.text
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": first_detected}


@responses.activate
def test_skipped_item_still_reported_as_seen(load_main):
    m = load_main()
    first_detected = ago(3700)
    cfg = tracked_config(m, make_config(watchers=(
        make_watcher("tagged", tags=["slow"], action=D.IGNORE),
    )), first_detected=first_detected)
    responses.get(DOWNLOAD_CLIENT_URL, json=QBIT_CLIENT)
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, status=500)
    qbit = m.QbitClient(QBIT, "admin", "pw")
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    assert m.handle_stalled_downloads(cfg, APP, qbit) == {"1"}
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": first_detected}


@responses.activate
def test_progress_skipped_item_still_reported_as_seen(load_main):
    m = load_main()
    first_detected = ago(3700)
    cfg = tracked_config(m, make_config(watchers=PROGRESS_WATCHERS),
                         first_detected=first_detected)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    assert m.handle_stalled_downloads(cfg, APP, None) == {"1"}
    assert calls("DELETE") == []
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": first_detected}


@responses.activate
def test_ignored_item_still_reported_as_seen(load_main):
    m = load_main()
    first_detected = ago(3700)
    cfg = tracked_config(m, make_config(watchers=(make_watcher(action=D.IGNORE),)),
                         first_detected=first_detected)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    assert m.handle_stalled_downloads(cfg, APP, None) == {"1"}
    assert calls("DELETE") == []
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": first_detected}


# --- reconciliation regression ---

@responses.activate
def test_recovered_download_gets_fresh_timer(load_main):
    m = load_main()
    cfg = make_config(watchers=(make_watcher(stalled_timeout=3600, action=D.REMOVE_AND_BLOCKLIST),))
    original = ago(3700)
    tracked_config(m, cfg, download_id="1", first_detected=original)

    responses.get(QUEUE_URL, json=queue_page([]))                        # cycle 1: recovered
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))   # cycle 2: stalled again

    seen = m.handle_stalled_downloads(cfg, APP, None)
    m.reconcile_tracked_downloads("Radarr0", seen, db_file=cfg.db_file)
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}

    m.handle_stalled_downloads(cfg, APP, None)

    assert calls("DELETE") == []
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file)["1"] > original


@responses.activate
def test_still_stalled_download_keeps_its_timer(load_main):
    m = load_main()
    cfg = make_config(watchers=(make_watcher(stalled_timeout=3600, action=D.REMOVE_AND_BLOCKLIST),))
    original = ago(100)
    tracked_config(m, cfg, download_id="1", first_detected=original)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))

    seen = m.handle_stalled_downloads(cfg, APP, None)

    assert m.reconcile_tracked_downloads("Radarr0", seen, db_file=cfg.db_file) == 0
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": original}


@responses.activate
def test_completed_download_row_pruned(load_main):
    m = load_main()
    cfg = make_config(watchers=(make_watcher(stalled_timeout=3600, action=D.REMOVE_AND_BLOCKLIST),))
    tracked_config(m, cfg, download_id="1", first_detected=ago(100))
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=2)]))

    seen = m.handle_stalled_downloads(cfg, APP, None)

    assert m.reconcile_tracked_downloads("Radarr0", seen, db_file=cfg.db_file) == 1
    assert list(m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file)) == ["2"]


@responses.activate
def test_flapping_download_is_never_actioned(load_main):
    m = load_main()
    cfg = make_config(watchers=(make_watcher(stalled_timeout=0, action=D.REMOVE_AND_BLOCKLIST),))
    m.initialize_database(cfg.db_file)
    responses.delete(f"{QUEUE_URL}/1", json={})
    for records in ([queue_item(item_id=1)], [], [queue_item(item_id=1)], []):
        responses.get(QUEUE_URL, json=queue_page(records))

    for _ in range(4):
        seen = m.handle_stalled_downloads(cfg, APP, None)
        m.reconcile_tracked_downloads("Radarr0", seen, db_file=cfg.db_file)

    assert calls("DELETE") == []
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}


# --- per-download grouping and DELETE failure handling ---

COMMAND_URL = f"{RADARR}/api/v3/command"
PACK_HASH = "PACKHASH"


def errors(caplog):
    return [r for r in caplog.records if r.levelno >= logging.ERROR]


def search_bodies():
    return [json.loads(c.request.body) for c in calls("POST")]


def sonarr_pack(m, action=D.KEEP_AND_BLOCKLIST_SEARCH, first_detected=None):
    """A Sonarr instance plus a two-record season pack, both rows tracked past timeout."""
    cfg = make_config(watchers=(make_watcher(action=action),))
    sonarr = make_app(type="sonarr", name="Sonarr1", url=RADARR, api_key="key")
    first_detected = first_detected or ago(3700)
    tracked_config(m, cfg, download_id="1", first_detected=first_detected, arr_service="Sonarr1")
    m.add_stalled_download_to_db("2", first_detected, "Sonarr1", db_file=cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([
        queue_item(item_id=1, download_id=PACK_HASH, episodeId=11),
        queue_item(item_id=2, download_id=PACK_HASH, episodeId=12),
    ]))
    return cfg, sonarr, first_detected


@responses.activate
def test_sibling_records_share_one_delete_and_search_each(load_main, caplog):
    m = load_main()
    cfg, sonarr, _ = sonarr_pack(m)
    responses.delete(f"{QUEUE_URL}/1", json={})
    responses.delete(f"{QUEUE_URL}/2", json={})
    responses.post(COMMAND_URL, json={})

    m.handle_stalled_downloads(cfg, sonarr, None)

    assert len(calls("DELETE")) == 1
    assert calls("DELETE")[0].request.url.startswith(f"{QUEUE_URL}/1?")
    assert search_bodies() == [{"name": "EpisodeSearch", "episodeIds": [11]},
                               {"name": "EpisodeSearch", "episodeIds": [12]}]
    assert m.get_stalled_downloads_from_db("Sonarr1", db_file=cfg.db_file) == {}
    assert errors(caplog) == []


@responses.activate
def test_sibling_within_timeout_is_not_actioned_with_the_group(load_main):
    m = load_main()
    cfg = make_config(watchers=(make_watcher(action=D.KEEP_AND_BLOCKLIST_SEARCH),))
    sonarr = make_app(type="sonarr", name="Sonarr1", url=RADARR, api_key="key")
    fresh = ago(10)
    tracked_config(m, cfg, download_id="1", arr_service="Sonarr1")
    m.add_stalled_download_to_db("2", fresh, "Sonarr1", db_file=cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([
        queue_item(item_id=1, download_id=PACK_HASH, episodeId=11),
        queue_item(item_id=2, download_id=PACK_HASH, episodeId=12),
    ]))
    responses.delete(f"{QUEUE_URL}/1", json={})
    responses.delete(f"{QUEUE_URL}/2", json={})
    responses.post(COMMAND_URL, json={})

    m.handle_stalled_downloads(cfg, sonarr, None)

    assert len(calls("DELETE")) == 1
    assert search_bodies() == [{"name": "EpisodeSearch", "episodeIds": [11]}]
    assert m.get_stalled_downloads_from_db("Sonarr1", db_file=cfg.db_file) == {"2": fresh}


@responses.activate
def test_delete_404_drops_row_without_error(load_main, caplog):
    m = load_main()
    cfg = tracked_config(m, make_config(watchers=(make_watcher(action=D.REMOVE_AND_BLOCKLIST),)))
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.delete(f"{QUEUE_URL}/1", json={}, status=404)

    with caplog.at_level(logging.INFO):
        m.handle_stalled_downloads(cfg, APP, None)

    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}
    assert "already removed" in caplog.text
    assert errors(caplog) == []


@responses.activate
def test_delete_500_retains_row_and_skips_search(load_main, caplog):
    m = load_main()
    first_detected = ago(3700)
    cfg = tracked_config(m, make_config(watchers=(make_watcher(action=D.REMOVE_AND_BLOCKLIST_SEARCH),)),
                         first_detected=first_detected)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1, movieId=770)]))
    responses.delete(f"{QUEUE_URL}/1", json={}, status=500)
    responses.post(COMMAND_URL, json={})

    m.handle_stalled_downloads(cfg, APP, None)

    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": first_detected}
    assert calls("POST") == []
    assert len(errors(caplog)) == 1


@responses.activate
def test_hashless_records_are_not_grouped_together(load_main):
    m = load_main()
    cfg = make_config(watchers=(make_watcher(action=D.REMOVE_AND_BLOCKLIST),))
    tracked_config(m, cfg, download_id="1")
    m.add_stalled_download_to_db("2", ago(3700), "Radarr0", db_file=cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([
        queue_item(item_id=1, download_id=None),
        queue_item(item_id=2, download_id=None),
    ]))
    responses.delete(f"{QUEUE_URL}/1", json={})
    responses.delete(f"{QUEUE_URL}/2", json={})

    m.handle_stalled_downloads(cfg, APP, None)

    assert len(calls("DELETE")) == 2
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}


@responses.activate
def test_group_delete_failure_leaves_no_member_actioned(load_main):
    m = load_main()
    cfg, sonarr, first_detected = sonarr_pack(m)
    responses.delete(f"{QUEUE_URL}/1", json={}, status=500)
    responses.delete(f"{QUEUE_URL}/2", json={})
    responses.post(COMMAND_URL, json={})

    m.handle_stalled_downloads(cfg, sonarr, None)

    assert len(calls("DELETE")) == 1
    assert calls("POST") == []
    assert m.get_stalled_downloads_from_db("Sonarr1", db_file=cfg.db_file) == {
        "1": first_detected, "2": first_detected}


@responses.activate
def test_retained_group_retries_and_succeeds_next_cycle(load_main):
    m = load_main()
    cfg, sonarr, first_detected = sonarr_pack(m)
    responses.delete(f"{QUEUE_URL}/1", json={}, status=500)   # cycle 1
    responses.delete(f"{QUEUE_URL}/1", json={})               # cycle 2
    responses.delete(f"{QUEUE_URL}/2", json={})
    responses.post(COMMAND_URL, json={})

    m.handle_stalled_downloads(cfg, sonarr, None)
    after_failure = m.get_stalled_downloads_from_db("Sonarr1", db_file=cfg.db_file)

    m.handle_stalled_downloads(cfg, sonarr, None)

    assert after_failure == {"1": first_detected, "2": first_detected}
    assert len(calls("DELETE")) == 2
    assert search_bodies() == [{"name": "EpisodeSearch", "episodeIds": [11]},
                               {"name": "EpisodeSearch", "episodeIds": [12]}]
    assert m.get_stalled_downloads_from_db("Sonarr1", db_file=cfg.db_file) == {}


@responses.activate
def test_retained_row_is_reconciled_once_the_download_leaves_the_queue(load_main):
    m = load_main()
    cfg = make_config(watchers=(make_watcher(action=D.REMOVE_AND_BLOCKLIST),))
    first_detected = ago(3700)
    tracked_config(m, cfg, download_id="1", first_detected=first_detected)
    responses.get(QUEUE_URL, json=queue_page([queue_item(item_id=1)]))
    responses.get(QUEUE_URL, json=queue_page([]))
    responses.delete(f"{QUEUE_URL}/1", json={}, status=500)

    seen = m.handle_stalled_downloads(cfg, APP, None)
    assert seen == {"1"}
    assert m.reconcile_tracked_downloads("Radarr0", seen, db_file=cfg.db_file) == 0
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {"1": first_detected}

    seen = m.handle_stalled_downloads(cfg, APP, None)

    assert seen == set()
    assert m.reconcile_tracked_downloads("Radarr0", seen, db_file=cfg.db_file) == 1
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}
    assert len(calls("DELETE")) == 1


@responses.activate
def test_metadata_flow_groups_sibling_records(load_main):
    m = load_main()
    cfg = make_config(count_metadata_as_stalled=True,
                      watchers=(make_watcher(action=D.REMOVE_AND_BLOCKLIST),))
    tracked_config(m, cfg, download_id="1")
    m.add_stalled_download_to_db("2", ago(3700), "Radarr0", db_file=cfg.db_file)
    responses.get(QUEUE_URL, json=queue_page([
        queue_item(item_id=1, error=METADATA_ERROR, download_id=PACK_HASH),
        queue_item(item_id=2, error=METADATA_ERROR, download_id=PACK_HASH),
    ]))
    responses.delete(f"{QUEUE_URL}/1", json={})
    responses.delete(f"{QUEUE_URL}/2", json={})

    m.detect_stuck_metadata_downloads(cfg, APP, None)

    assert query()["status"] == ["queued"]
    assert len(calls("DELETE")) == 1
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=cfg.db_file) == {}
