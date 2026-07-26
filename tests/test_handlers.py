import json
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import responses

import config
from config import QueueItemDisposition as D
from conftest import make_app, make_config, make_watcher, queue_item, queue_page

RADARR = "http://radarr:7878"
QUEUE_URL = f"{RADARR}/api/v3/queue"
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


def qbit_client(m, tags_pages):
    """A QbitClient wired to mocked login + torrents/info responses (one page per lookup)."""
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

    assert m.match_watcher(queue_item(), watchers, None) is m.SKIP_ITEM


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
