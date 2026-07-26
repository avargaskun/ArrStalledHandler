import textwrap

import pytest

import config
from config import QueueItemDisposition as Disposition


def write_yaml(tmp_path, text, name="config.yaml"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text))
    return str(path)


def test_service_urls_comma_split(load_main):
    m = load_main({
        "RADARR_URL": "http://a,http://b",
        "RADARR_API_KEY": "k1,k2",
        "SONARR_URL": "http://c,http://d",
        "SONARR_API_KEY": "k3,k4",
    })

    assert m.RADARR_URL == ["http://a", "http://b"]
    assert m.RADARR_API_KEY == ["k1", "k2"]
    assert m.SONARR_URL == ["http://c", "http://d"]
    assert m.SONARR_API_KEY == ["k3", "k4"]


def test_unset_service_is_none(load_main):
    m = load_main({"RADARR_URL": "http://a", "RADARR_API_KEY": "k1"})

    assert m.SONARR_URL is None
    assert m.LIDARR_URL is None
    assert m.READARR_URL is None


def test_defaults(load_main):
    m = load_main({})

    assert m.STALLED_TIMEOUT == 3600
    assert m.STALLED_ACTION == "BLOCKLIST_AND_SEARCH"
    assert m.RUN_INTERVAL == 300
    assert m.VERBOSE is False
    assert m.COUNT_DOWNLOADING_METADATA_AS_STALLED is False


def test_stalled_action_uppercased(load_main):
    m = load_main({"STALLED_ACTION": "remove"})

    assert m.STALLED_ACTION == "REMOVE"


def test_ignore_tags_parsed_and_stripped(load_main):
    m = load_main({"IGNORE_TORRENT_TAGS": " slow , ,manual"})
    assert m.IGNORE_TORRENT_TAGS == ["slow", "manual"]

    m = load_main({})
    assert m.IGNORE_TORRENT_TAGS == []


def test_verbose_truthy_parsing(load_main):
    assert load_main({"VERBOSE": "TRUE"}).VERBOSE is True
    assert load_main({"VERBOSE": "no"}).VERBOSE is False


def test_url_without_key_exits(load_main):
    with pytest.raises(SystemExit) as excinfo:
        load_main({"RADARR_URL": "http://a"})

    message = str(excinfo.value)
    assert "RADARR_URL" in message
    assert "RADARR_API_KEY" in message


def test_url_key_count_mismatch_exits(load_main):
    with pytest.raises(SystemExit) as excinfo:
        load_main({"RADARR_URL": "http://a,http://b", "RADARR_API_KEY": "k1"})

    message = str(excinfo.value)
    assert "2" in message
    assert "1" in message


def test_empty_url_disables_service(load_main):
    m = load_main({"RADARR_URL": ""})

    assert m.RADARR_URL is None
    assert m.RADARR_API_KEY is None


def test_empty_scalars_use_defaults(load_main):
    m = load_main({"STALLED_TIMEOUT": "", "RUN_INTERVAL": "", "STALLED_ACTION": ""})

    assert m.STALLED_TIMEOUT == 3600
    assert m.RUN_INTERVAL == 300
    assert m.STALLED_ACTION == "BLOCKLIST_AND_SEARCH"


@pytest.mark.parametrize("value,expected", [
    ("90", 90),
    (90, 90),
    ("5m", 300),
    ("1h30m", 5400),
    ("2d", 172800),
    ("45s", 45),
    ("1d2h3m4s", 93784),
    (" 5m ", 300),
])
def test_parse_duration_accepts(value, expected):
    assert config.parse_duration(value) == expected


@pytest.mark.parametrize("value", ["", "5x", "m5", "1.5h", "-5m", 0, -1, "0", "30m1h", None, 1.5, True])
def test_parse_duration_rejects(value):
    with pytest.raises(ValueError) as excinfo:
        config.parse_duration(value)

    assert repr(value) in str(excinfo.value)


EXPECTED_PARAMS = {
    Disposition.REMOVE: ("true", "false", "false", "false"),
    Disposition.REMOVE_AND_BLOCKLIST: ("true", "false", "true", "true"),
    Disposition.REMOVE_AND_BLOCKLIST_SEARCH: ("true", "false", "true", "false"),
    Disposition.CHANGE_CATEGORY: ("false", "true", "false", "false"),
    Disposition.CHANGE_CATEGORY_AND_BLOCKLIST: ("false", "true", "true", "true"),
    Disposition.CHANGE_CATEGORY_AND_BLOCKLIST_SEARCH: ("false", "true", "true", "false"),
    Disposition.KEEP: ("false", "false", "false", "false"),
    Disposition.KEEP_AND_BLOCKLIST: ("false", "false", "true", "true"),
    Disposition.KEEP_AND_BLOCKLIST_SEARCH: ("false", "false", "true", "false"),
}


@pytest.mark.parametrize("disposition,expected", list(EXPECTED_PARAMS.items()))
def test_disposition_as_params(disposition, expected):
    remove, category, blocklist, skip = expected

    assert disposition.as_params() == {
        "removeFromClient": remove,
        "changeCategory": category,
        "blocklist": blocklist,
        "skipRedownload": skip,
    }


def test_all_dispositions_covered():
    assert set(EXPECTED_PARAMS) | {Disposition.IGNORE} == set(Disposition)


def test_ignore_has_no_params():
    with pytest.raises(ValueError):
        Disposition.IGNORE.as_params()


def test_triggers_search_only_for_search_members():
    searching = {d for d in Disposition if d.triggers_search}

    assert searching == {
        Disposition.REMOVE_AND_BLOCKLIST_SEARCH,
        Disposition.CHANGE_CATEGORY_AND_BLOCKLIST_SEARCH,
        Disposition.KEEP_AND_BLOCKLIST_SEARCH,
    }


@pytest.mark.parametrize("value,expected", [
    ("REMOVE", Disposition.REMOVE),
    ("remove", Disposition.REMOVE),
    ("BLOCKLIST", Disposition.REMOVE_AND_BLOCKLIST),
    ("blocklist_and_search", Disposition.REMOVE_AND_BLOCKLIST_SEARCH),
    ("BLOCKLIST_AND_SEARCH", Disposition.REMOVE_AND_BLOCKLIST_SEARCH),
    ("KEEP", Disposition.KEEP),
    ("keep_and_blocklist", Disposition.KEEP_AND_BLOCKLIST),
    ("Change_Category", Disposition.CHANGE_CATEGORY),
    ("ignore", Disposition.IGNORE),
    (" remove ", Disposition.REMOVE),
])
def test_disposition_parse(value, expected):
    assert Disposition.parse(value) is expected


def test_disposition_parse_passes_through_enum():
    assert Disposition.parse(Disposition.KEEP) is Disposition.KEEP


@pytest.mark.parametrize("value", ["BOGUS", "", "remove_and", 7, None])
def test_disposition_parse_rejects_unknown(value):
    with pytest.raises(ValueError) as excinfo:
        Disposition.parse(value)

    assert repr(value) in str(excinfo.value)


FULL_YAML = """
    version: 1

    runInterval: 5m
    log:
      verbose: true
    healthCheck:
      enabled: false
      port: 8080
    dbFile: /data/stalled.db
    countDownloadingMetadataAsStalled: true

    arrApps:
      - type: radarr
        name: anime
        url: localhost:7878
        apiKey: xxxyyyzzz
        forceSearch: false
      - type: sonarr
        name: shows
        url: https://sonarr.example.com
        apiKey: aaabbbccc

    downloaders:
      - type: qbittorrent
        name: default
        url: localhost:9999
        username: admin
        password: abcd

    watchers:
      - name: seeding-protection
        tags: [keep, private]
        stalledTimeout: 1h
        action: IGNORE
      - name: catch-all
        action: blocklist_and_search
"""


def test_full_config_parses(tmp_path):
    model = config._load_yaml_model(write_yaml(tmp_path, FULL_YAML))

    assert model.version == 1
    assert model.runInterval == 300
    assert model.log.verbose is True
    assert model.healthCheck.enabled is False
    assert model.healthCheck.port == 8080
    assert model.dbFile == "/data/stalled.db"
    assert model.countDownloadingMetadataAsStalled is True

    radarr, sonarr = model.arrApps
    assert (radarr.type, radarr.name, radarr.apiKey) == ("radarr", "anime", "xxxyyyzzz")
    assert radarr.url == "http://localhost:7878"
    assert radarr.forceSearch is False
    assert sonarr.url == "https://sonarr.example.com"
    assert sonarr.forceSearch is True

    downloader, = model.downloaders
    assert (downloader.type, downloader.name) == ("qbittorrent", "default")
    assert downloader.url == "http://localhost:9999"
    assert (downloader.username, downloader.password) == ("admin", "abcd")

    protection, catch_all = model.watchers
    assert protection.tags == ["keep", "private"]
    assert protection.stalledTimeout == 3600
    assert protection.action is Disposition.IGNORE
    assert catch_all.tags == []
    assert catch_all.stalledTimeout == 3600
    assert catch_all.action is Disposition.REMOVE_AND_BLOCKLIST_SEARCH


def test_minimal_config_leaves_sections_absent(tmp_path):
    model = config._load_yaml_model(write_yaml(tmp_path, "version: 1\n"))

    assert model.arrApps is None
    assert model.downloaders is None
    assert model.watchers is None
    assert model.log is None
    assert model.healthCheck is None
    assert model.dbFile is None
    assert model.runInterval is None
    assert model.countDownloadingMetadataAsStalled is None


def test_missing_file_returns_none(tmp_path):
    assert config._load_yaml_model(str(tmp_path / "nope.yaml")) is None


def test_healthcheck_defaults(tmp_path):
    model = config._load_yaml_model(write_yaml(tmp_path, """
        version: 1
        healthCheck: {}
    """))

    assert model.healthCheck.enabled is True
    assert model.healthCheck.port == 9898


def test_run_interval_accepts_bare_seconds(tmp_path):
    model = config._load_yaml_model(write_yaml(tmp_path, "version: 1\nrunInterval: 90\n"))

    assert model.runInterval == 90


def assert_config_error(tmp_path, text, *expected_fragments):
    with pytest.raises(config.ConfigError) as excinfo:
        config._load_yaml_model(write_yaml(tmp_path, text))

    joined = " | ".join(excinfo.value.messages)
    for fragment in expected_fragments:
        assert fragment in joined, joined
    return excinfo.value


def test_malformed_yaml(tmp_path):
    assert_config_error(tmp_path, "version: 1\n  bad: [unclosed\n", "invalid YAML")


def test_non_mapping_root(tmp_path):
    assert_config_error(tmp_path, "- version: 1\n", "mapping")


def test_empty_file_is_error(tmp_path):
    assert_config_error(tmp_path, "", "mapping")


def test_wrong_version(tmp_path):
    assert_config_error(tmp_path, "version: 2\n", "version", "only version 1")


def test_missing_version(tmp_path):
    assert_config_error(tmp_path, "dbFile: x.db\n", "version")


def test_unknown_root_key(tmp_path):
    assert_config_error(tmp_path, "version: 1\nbogus: 3\n", "bogus")


def test_unknown_nested_key(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        arrApps:
          - type: radarr
            name: a
            url: http://a
            apiKey: k
            bogus: 1
    """, "arrApps.0.bogus")


def test_missing_name(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        arrApps:
          - type: radarr
            url: http://a
            apiKey: k
    """, "arrApps.0.name")


def test_empty_name(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        watchers:
          - name: "  "
    """, "watchers.0.name")


def test_missing_api_key(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        arrApps:
          - type: radarr
            name: a
            url: http://a
    """, "arrApps.0.apiKey")


def test_bad_arr_type(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        arrApps:
          - type: plexarr
            name: a
            url: http://a
            apiKey: k
    """, "arrApps.0.type")


def test_empty_url(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        arrApps:
          - type: radarr
            name: a
            url: ""
            apiKey: k
    """, "arrApps.0.url")


def test_duplicate_watcher_names(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        watchers:
          - name: dup
          - name: dup
    """, "watchers", "dup")


def test_duplicate_arr_app_names(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        arrApps:
          - type: radarr
            name: same
            url: http://a
            apiKey: k
          - type: sonarr
            name: same
            url: http://b
            apiKey: k
    """, "arrApps", "same")


def test_empty_arr_apps_section(tmp_path):
    assert_config_error(tmp_path, "version: 1\narrApps: []\n", "arrApps", "empty")


def test_empty_watchers_section(tmp_path):
    assert_config_error(tmp_path, "version: 1\nwatchers: []\n", "watchers", "empty")


def test_two_downloaders(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        downloaders:
          - type: qbittorrent
            name: one
            url: http://a
          - type: qbittorrent
            name: two
            url: http://b
    """, "downloaders", "one entry")


def test_bad_action(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        watchers:
          - name: w
            action: EXPLODE
    """, "watchers.0.action", "EXPLODE")


def test_bad_duration(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        watchers:
          - name: w
            stalledTimeout: 5x
    """, "watchers.0.stalledTimeout", "5x")


def test_bad_run_interval(tmp_path):
    assert_config_error(tmp_path, "version: 1\nrunInterval: -3\n", "runInterval")


def test_multiple_errors_reported(tmp_path):
    error = assert_config_error(tmp_path, """
        version: 2
        bogus: true
    """, "version", "bogus")

    assert len(error.messages) == 2


def test_explicit_null_run_interval_stays_none(tmp_path):
    model = config._load_yaml_model(write_yaml(tmp_path, "version: 1\nrunInterval:\n"))

    assert model.runInterval is None


def test_unreadable_path_is_error(tmp_path):
    directory = tmp_path / "config.yaml"
    directory.mkdir()

    with pytest.raises(config.ConfigError) as excinfo:
        config._load_yaml_model(str(directory))

    assert "could not be read" in excinfo.value.messages[0]
