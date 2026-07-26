import logging
import textwrap

import pytest

import config
from config import QueueItemDisposition as Disposition
from conftest import ENV_VARS


def write_yaml(tmp_path, text, name="config.yaml"):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text))
    return str(path)


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


MISSING = "missing.yaml"


def load(tmp_path, environ, yaml_text=None, config_path=None):
    """Run load_config against an optional YAML file and an explicit environment."""
    path = write_yaml(tmp_path, yaml_text) if yaml_text is not None else str(tmp_path / MISSING)
    return config.load_config(config_path if config_path is not None else path, environ=environ)


FULL_ENV = {
    "RADARR_URL": "http://radarr-a,http://radarr-b",
    "RADARR_API_KEY": "k1,k2",
    "SONARR_URL": "http://sonarr",
    "SONARR_API_KEY": "k3",
    "QBITTORRENT_URL": "qbit:8080",
    "QBITTORRENT_USERNAME": "admin",
    "QBITTORRENT_PASSWORD": "secret",
    "STALLED_TIMEOUT": "30m",
    "STALLED_ACTION": "blocklist",
    "RUN_INTERVAL": "120",
    "VERBOSE": "TRUE",
    "COUNT_DOWNLOADING_METADATA_AS_STALLED": "true",
}


def test_env_only_config(tmp_path):
    cfg = load(tmp_path, FULL_ENV)

    assert [(a.type, a.name, a.url, a.api_key) for a in cfg.arr_apps] == [
        ("radarr", "Radarr0", "http://radarr-a", "k1"),
        ("radarr", "Radarr1", "http://radarr-b", "k2"),
        ("sonarr", "Sonarr0", "http://sonarr", "k3"),
    ]
    assert all(app.force_search for app in cfg.arr_apps)
    assert cfg.downloader == config.Downloader("default", "http://qbit:8080", "admin", "secret")
    assert cfg.run_interval == 120
    assert cfg.verbose is True
    assert cfg.count_metadata_as_stalled is True
    assert cfg.health_enabled is True
    assert cfg.health_port == 9898
    assert cfg.db_file == "stalled_downloads.db"

    watcher, = cfg.watchers
    assert watcher.name == "env-default"
    assert watcher.tags == ()
    assert watcher.stalled_timeout == 1800
    assert watcher.action is Disposition.REMOVE_AND_BLOCKLIST


def test_env_only_defaults(tmp_path):
    cfg = load(tmp_path, {"LIDARR_URL": "http://l", "LIDARR_API_KEY": "k"})

    assert cfg.run_interval == 300
    assert cfg.verbose is False
    assert cfg.count_metadata_as_stalled is False
    assert cfg.downloader is None
    assert [(a.name, a.api_version) for a in cfg.arr_apps] == [("Lidarr0", "v1")]
    assert cfg.watchers[0].stalled_timeout == 3600
    assert cfg.watchers[0].action is Disposition.REMOVE_AND_BLOCKLIST_SEARCH


def test_empty_scalars_use_defaults_in_load_config(tmp_path):
    cfg = load(tmp_path, {
        "RADARR_URL": "http://a", "RADARR_API_KEY": "k",
        "STALLED_TIMEOUT": "", "RUN_INTERVAL": "", "STALLED_ACTION": "",
        "VERBOSE": "", "COUNT_DOWNLOADING_METADATA_AS_STALLED": "",
        "QBITTORRENT_URL": "", "IGNORE_TORRENT_TAGS": "", "SONARR_URL": "",
    })

    assert cfg.run_interval == 300
    assert cfg.verbose is False
    assert cfg.count_metadata_as_stalled is False
    assert cfg.downloader is None
    assert [a.name for a in cfg.arr_apps] == ["Radarr0"]
    assert cfg.watchers[0].stalled_timeout == 3600
    assert cfg.watchers[0].action is Disposition.REMOVE_AND_BLOCKLIST_SEARCH


@pytest.mark.parametrize("value,expected", [
    ("REMOVE", Disposition.REMOVE),
    ("BLOCKLIST", Disposition.REMOVE_AND_BLOCKLIST),
    ("BLOCKLIST_AND_SEARCH", Disposition.REMOVE_AND_BLOCKLIST_SEARCH),
    ("keep_and_blocklist", Disposition.KEEP_AND_BLOCKLIST),
])
def test_legacy_stalled_action_mapping(tmp_path, value, expected):
    cfg = load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k", "STALLED_ACTION": value})

    assert cfg.watchers[-1].action is expected


def test_ignore_tags_synthesizes_leading_watcher(tmp_path):
    cfg = load(tmp_path, {
        "RADARR_URL": "http://a", "RADARR_API_KEY": "k",
        "QBITTORRENT_URL": "http://q",
        "IGNORE_TORRENT_TAGS": " slow , ,manual",
    })

    ignore, default = cfg.watchers
    assert ignore.name == "env-ignore-tags"
    assert ignore.tags == ("slow", "manual")
    assert ignore.action is Disposition.IGNORE
    assert default.name == "env-default"


def test_no_ignore_tags_means_single_watcher(tmp_path):
    cfg = load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k"})

    assert [w.name for w in cfg.watchers] == ["env-default"]


YAML_ARR = """
    version: 1
    arrApps:
      - type: sonarr
        name: shows
        url: sonarr.local:8989
        apiKey: yamlkey
        forceSearch: false
"""


def test_yaml_arr_apps_ignore_env(tmp_path):
    cfg = load(tmp_path, dict(FULL_ENV), YAML_ARR)

    app, = cfg.arr_apps
    assert (app.type, app.name, app.url, app.api_key) == ("sonarr", "shows", "http://sonarr.local:8989", "yamlkey")
    assert app.force_search is False
    assert app.api_version == "v3"


def test_yaml_scalars_win_env_fills_gaps(tmp_path):
    cfg = load(tmp_path, dict(FULL_ENV), """
        version: 1
        runInterval: 10m
        healthCheck:
          enabled: false
          port: 8080
        dbFile: /data/x.db
    """)

    assert cfg.run_interval == 600
    assert cfg.health_enabled is False
    assert cfg.health_port == 8080
    assert cfg.db_file == "/data/x.db"
    # No log/countDownloadingMetadataAsStalled keys: env still applies.
    assert cfg.verbose is True
    assert cfg.count_metadata_as_stalled is True
    # No arrApps/downloaders/watchers sections: env still applies.
    assert [a.name for a in cfg.arr_apps] == ["Radarr0", "Radarr1", "Sonarr0"]
    assert cfg.downloader is not None
    assert cfg.watchers[-1].name == "env-default"


def test_version_only_yaml_uses_env(tmp_path):
    cfg = load(tmp_path, dict(FULL_ENV), "version: 1\n")

    assert cfg.run_interval == 120
    assert cfg.verbose is True
    assert cfg.watchers[-1].stalled_timeout == 1800
    assert [a.name for a in cfg.arr_apps] == ["Radarr0", "Radarr1", "Sonarr0"]


def test_yaml_watchers_ignore_env_watcher_vars(tmp_path):
    cfg = load(tmp_path, dict(FULL_ENV, IGNORE_TORRENT_TAGS="slow"), """
        version: 1
        watchers:
          - name: protected
            tags: [keep]
            action: IGNORE
          - name: fallback
            stalledTimeout: 45m
            action: KEEP
    """)

    protected, fallback = cfg.watchers
    assert (protected.name, protected.tags, protected.action) == ("protected", ("keep",), Disposition.IGNORE)
    assert (fallback.stalled_timeout, fallback.action) == (2700, Disposition.KEEP)


def test_yaml_downloader_ignores_env(tmp_path):
    cfg = load(tmp_path, dict(FULL_ENV), """
        version: 1
        downloaders:
          - type: qbittorrent
            name: box
            url: yamlqbit:9999
    """)

    assert cfg.downloader == config.Downloader("box", "http://yamlqbit:9999", None, None)


def test_yaml_verbose_overrides_env(tmp_path):
    cfg = load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k", "VERBOSE": "true"}, """
        version: 1
        log:
          verbose: false
    """)

    assert cfg.verbose is False


def test_implicit_default_appended_when_last_watcher_tagged(tmp_path):
    cfg = load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k"}, """
        version: 1
        watchers:
          - name: protected
            tags: [keep]
            action: IGNORE
    """)

    assert [w.name for w in cfg.watchers] == ["protected", "implicit-default"]
    implicit = cfg.watchers[-1]
    assert implicit.tags == ()
    assert implicit.stalled_timeout == 3600
    assert implicit.action is Disposition.REMOVE_AND_BLOCKLIST_SEARCH


def test_implicit_default_not_appended_when_catch_all_present(tmp_path):
    cfg = load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k"}, """
        version: 1
        watchers:
          - name: protected
            tags: [keep]
          - name: catch-all
            action: IGNORE
    """)

    assert [w.name for w in cfg.watchers] == ["protected", "catch-all"]


def test_config_file_env_var_resolves_path(tmp_path):
    path = write_yaml(tmp_path, YAML_ARR, name="custom.yaml")
    cfg = config.load_config(environ={"CONFIG_FILE": path})

    assert [a.name for a in cfg.arr_apps] == ["shows"]


def test_explicit_path_wins_over_config_file_env(tmp_path):
    other = write_yaml(tmp_path, YAML_ARR, name="other.yaml")
    cfg = config.load_config(other, environ={"CONFIG_FILE": str(tmp_path / "nope.yaml"),
                                             "RADARR_URL": "http://a", "RADARR_API_KEY": "k"})

    assert [a.name for a in cfg.arr_apps] == ["shows"]


def assert_exits(caplog, *fragments, **kwargs):
    with caplog.at_level(logging.ERROR):
        with pytest.raises(SystemExit) as excinfo:
            config.load_config(**kwargs)

    assert excinfo.value.code == 1
    errors = " | ".join(r.message for r in caplog.records if r.levelno == logging.ERROR)
    for fragment in fragments:
        assert fragment in errors, errors
    return errors


def test_url_key_mismatch_exits(tmp_path, caplog):
    assert_exits(caplog, "RADARR_URL has 2 entries", "RADARR_API_KEY has 1",
                 config_path=str(tmp_path / MISSING),
                 environ={"RADARR_URL": "http://a,http://b", "RADARR_API_KEY": "k"})


def test_url_without_key_exits(tmp_path, caplog):
    assert_exits(caplog, "RADARR_URL", "RADARR_API_KEY",
                 config_path=str(tmp_path / MISSING),
                 environ={"RADARR_URL": "http://a"})


def test_no_instances_exits(tmp_path, caplog):
    assert_exits(caplog, "no *arr instances configured",
                 config_path=str(tmp_path / MISSING), environ={})


def test_invalid_stalled_action_exits(tmp_path, caplog):
    assert_exits(caplog, "STALLED_ACTION", "EXPLODE",
                 config_path=str(tmp_path / MISSING),
                 environ={"RADARR_URL": "http://a", "RADARR_API_KEY": "k", "STALLED_ACTION": "EXPLODE"})


def test_invalid_stalled_timeout_exits(tmp_path, caplog):
    assert_exits(caplog, "STALLED_TIMEOUT", "5x",
                 config_path=str(tmp_path / MISSING),
                 environ={"RADARR_URL": "http://a", "RADARR_API_KEY": "k", "STALLED_TIMEOUT": "5x"})


def test_invalid_run_interval_exits(tmp_path, caplog):
    assert_exits(caplog, "RUN_INTERVAL", "0",
                 config_path=str(tmp_path / MISSING),
                 environ={"RADARR_URL": "http://a", "RADARR_API_KEY": "k", "RUN_INTERVAL": "0"})


def test_invalid_yaml_exits(tmp_path, caplog):
    assert_exits(caplog, "version",
                 config_path=write_yaml(tmp_path, "version: 2\n"),
                 environ={"RADARR_URL": "http://a", "RADARR_API_KEY": "k"})


def test_multiple_env_problems_reported_together(tmp_path, caplog):
    errors = assert_exits(caplog, "STALLED_ACTION", "no *arr instances configured",
                          config_path=str(tmp_path / MISSING),
                          environ={"STALLED_ACTION": "EXPLODE"})

    assert errors.count("|") >= 1


def test_empty_env_url_entry_exits(tmp_path, caplog):
    assert_exits(caplog, "RADARR_URL entry 1",
                 config_path=str(tmp_path / MISSING),
                 environ={"RADARR_URL": "http://a, ", "RADARR_API_KEY": "k1,k2"})


def test_warns_when_tags_without_downloader(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k",
                        "IGNORE_TORRENT_TAGS": "slow"})

    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("no download client is configured" in message for message in warnings)


def test_no_tag_warning_when_downloader_present(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k",
                        "QBITTORRENT_URL": "http://q", "IGNORE_TORRENT_TAGS": "slow"})

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_warns_change_category_with_lidarr(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        load(tmp_path, {"LIDARR_URL": "http://l", "LIDARR_API_KEY": "k"}, """
            version: 1
            watchers:
              - name: recat
                action: CHANGE_CATEGORY_AND_BLOCKLIST
        """)

    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("CHANGE_CATEGORY" in message for message in warnings)


def test_no_change_category_warning_without_lidarr(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k"}, """
            version: 1
            watchers:
              - name: recat
                action: CHANGE_CATEGORY
        """)

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_config_source_logged(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k"})
    assert any("using environment variables only" in r.message for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.INFO):
        load(tmp_path, {}, YAML_ARR)
    assert any("Loaded configuration from" in r.message for r in caplog.records)


def test_app_config_is_frozen(tmp_path):
    cfg = load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k"})

    with pytest.raises(Exception):
        cfg.run_interval = 5


def test_yaml_count_metadata_overrides_env(tmp_path):
    cfg = load(tmp_path, dict(FULL_ENV), """
        version: 1
        countDownloadingMetadataAsStalled: false
    """)

    assert cfg.count_metadata_as_stalled is False


def test_defaults_to_os_environ(tmp_path, monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("RADARR_URL", "http://from-os-environ")
    monkeypatch.setenv("RADARR_API_KEY", "k")

    cfg = config.load_config(str(tmp_path / MISSING))

    assert [a.url for a in cfg.arr_apps] == ["http://from-os-environ"]


def test_importing_main_has_no_config_side_effects(monkeypatch, tmp_path, caplog):
    """main.py must be import-safe: no env reads, no config load, no logging config."""
    import importlib
    import logging as logging_module

    import dotenv

    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.chdir(tmp_path)

    called = []
    monkeypatch.setattr(config, "load_config", lambda *a, **k: called.append(1))
    monkeypatch.setattr(logging_module, "basicConfig", lambda *a, **k: called.append(1))

    import main
    with caplog.at_level(logging.DEBUG):
        reloaded = importlib.reload(main)

    assert called == []
    assert caplog.records == []
    assert reloaded.main is not None
