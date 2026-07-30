import logging
import pathlib
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
    model = config._load_yaml_model(write_yaml(tmp_path, FULL_YAML), {})

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
    model = config._load_yaml_model(write_yaml(tmp_path, "version: 1\n"), {})

    assert model.arrApps is None
    assert model.downloaders is None
    assert model.watchers is None
    assert model.log is None
    assert model.healthCheck is None
    assert model.dbFile is None
    assert model.runInterval is None
    assert model.countDownloadingMetadataAsStalled is None


def test_missing_file_returns_none(tmp_path):
    assert config._load_yaml_model(str(tmp_path / "nope.yaml"), {}) is None


def test_healthcheck_defaults(tmp_path):
    model = config._load_yaml_model(write_yaml(tmp_path, """
        version: 1
        healthCheck: {}
    """), {})

    assert model.healthCheck.enabled is True
    assert model.healthCheck.port == 9898


def test_run_interval_accepts_bare_seconds(tmp_path):
    model = config._load_yaml_model(write_yaml(tmp_path, "version: 1\nrunInterval: 90\n"), {})

    assert model.runInterval == 90


def assert_config_error(tmp_path, text, *expected_fragments):
    with pytest.raises(config.ConfigError) as excinfo:
        config._load_yaml_model(write_yaml(tmp_path, text), {})

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


@pytest.mark.parametrize("bounds,field", [
    ("maxProgress: 101", "watchers.0.maxProgress"),
    ("minProgress: -1", "watchers.0.minProgress"),
])
def test_watcher_progress_out_of_range_rejected(tmp_path, bounds, field):
    assert_config_error(tmp_path, f"""
        version: 1
        watchers:
          - name: w
            {bounds}
    """, field)


def test_watcher_progress_non_numeric_rejected(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        watchers:
          - name: w
            maxProgress: lots
    """, "watchers.0.maxProgress")


def test_watcher_progress_boolean_rejected(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        watchers:
          - name: w
            maxProgress: yes
    """, "watchers.0.maxProgress", "not a boolean")


def test_watcher_min_above_max_rejected(tmp_path):
    assert_config_error(tmp_path, """
        version: 1
        watchers:
          - name: w
            minProgress: 50
            maxProgress: 10
    """, "watchers.0", "minProgress (50.0) must not exceed maxProgress (10.0)")


def test_bad_run_interval(tmp_path):
    assert_config_error(tmp_path, "version: 1\nrunInterval: -3\n", "runInterval")


def test_multiple_errors_reported(tmp_path):
    error = assert_config_error(tmp_path, """
        version: 2
        bogus: true
    """, "version", "bogus")

    assert len(error.messages) == 2


def test_explicit_null_run_interval_stays_none(tmp_path):
    model = config._load_yaml_model(write_yaml(tmp_path, "version: 1\nrunInterval:\n"), {})

    assert model.runInterval is None


def test_unreadable_path_is_error(tmp_path):
    directory = tmp_path / "config.yaml"
    directory.mkdir()

    with pytest.raises(config.ConfigError) as excinfo:
        config._load_yaml_model(str(directory), {})

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


def test_watcher_progress_bounds_parsed(tmp_path):
    cfg = load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k"}, """
        version: 1
        watchers:
          - name: bounded
            minProgress: 5
            maxProgress: 95.5
          - name: catch-all
            action: KEEP
    """)

    bounded, catch_all = cfg.watchers
    assert (bounded.min_progress, bounded.max_progress) == (5.0, 95.5)
    assert (catch_all.min_progress, catch_all.max_progress) == (None, None)


def test_watcher_progress_env_substitution(tmp_path):
    cfg = load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k", "T": "5"}, """
        version: 1
        watchers:
          - name: bounded
            maxProgress: ${T}
          - name: catch-all
            action: KEEP
    """)

    assert cfg.watchers[0].max_progress == 5.0


def test_trailing_progress_watcher_gets_implicit_catch_all(tmp_path):
    cfg = load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k"}, """
        version: 1
        watchers:
          - name: nothing-downloaded
            maxProgress: 0
    """)

    assert [w.name for w in cfg.watchers] == ["nothing-downloaded", "implicit-default"]
    assert cfg.watchers[-1] is config.IMPLICIT_DEFAULT_WATCHER


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


def test_url_key_mismatch_does_not_also_claim_nothing_configured(tmp_path, caplog):
    errors = assert_exits(caplog, "RADARR_URL has 2 entries",
                          config_path=str(tmp_path / MISSING),
                          environ={"RADARR_URL": "http://a,http://b", "RADARR_API_KEY": "k"})

    assert "no *arr instances configured" not in errors


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


def test_invalid_health_port_exits(tmp_path, caplog):
    assert_exits(caplog, "healthCheck.port",
                 config_path=write_yaml(tmp_path, "version: 1\nhealthCheck:\n  port: 99999\n"),
                 environ={"RADARR_URL": "http://a", "RADARR_API_KEY": "k"})


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
                        "QBITTORRENT_URL": "http://q", "IGNORE_TORRENT_TAGS": "slow"},
             "version: 1\n")

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


def test_missing_requested_config_file_warns(tmp_path, caplog):
    missing = str(tmp_path / "nope.yaml")

    with caplog.at_level(logging.INFO):
        config.load_config(missing, environ={"RADARR_URL": "http://a", "RADARR_API_KEY": "k"})

    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(missing in message for message in warnings)


def test_missing_config_file_from_env_warns(tmp_path, caplog):
    missing = str(tmp_path / "nope.yaml")

    with caplog.at_level(logging.INFO):
        config.load_config(environ={"CONFIG_FILE": missing,
                                    "RADARR_URL": "http://a", "RADARR_API_KEY": "k"})

    warnings = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(missing in message for message in warnings)


def test_missing_default_config_file_stays_info(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        config.load_config(environ={"RADARR_URL": "http://a", "RADARR_API_KEY": "k"})

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
    assert any(config.DEFAULT_CONFIG_PATH in r.message for r in caplog.records)


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


SECRET_KEY = "SUPERSECRETAPIKEY"
SECRET_PASSWORD = "SUPERSECRETPASSWORD"

YAML_SECRETS_WITH_ERRORS = f"""
version: 1
arrApps:
  - type: bogus
    name: a
    url: localhost:7878
    apiKey: {SECRET_KEY}
    forceSearch: notabool
downloaders:
  - type: qbittorrent
    name: d
    url: localhost:8080
    username: admin
    password: {SECRET_PASSWORD}
    bogusKey: 1
watchers:
  - name: w
    stalledTimeout: 5x
    action: NOPE
"""


def test_validation_errors_never_log_secrets(tmp_path, caplog):
    errors = assert_exits(caplog, "arrApps.0.type", "downloaders.0.bogusKey",
                          "watchers.0.stalledTimeout", "watchers.0.action",
                          config_path=write_yaml(tmp_path, YAML_SECRETS_WITH_ERRORS),
                          environ={})

    assert SECRET_KEY not in errors
    assert SECRET_PASSWORD not in errors


def test_shipped_example_yaml_is_valid():
    """example.yaml is the canonical schema documentation; it must actually load."""
    example = pathlib.Path(__file__).resolve().parent.parent / "example.yaml"
    cfg = config.load_config(str(example), environ={"ANIME_RADARR_API_KEY": "abc123"})

    assert [a.name for a in cfg.arr_apps] == ["anime-movies", "tv"]
    assert cfg.arr_apps[0].api_key == "abc123"
    assert cfg.arr_apps[1].url == "http://localhost:8989"
    assert cfg.downloader.name == "default"
    assert cfg.watchers[0].action is config.QueueItemDisposition.IGNORE
    assert cfg.watchers[-1].tags == ()


def test_bad_api_key_type_does_not_log_its_value(tmp_path, caplog):
    errors = assert_exits(caplog, "arrApps.0.apiKey",
                          config_path=write_yaml(tmp_path, f"""
version: 1
arrApps:
  - type: radarr
    name: a
    url: localhost:7878
    apiKey: [{SECRET_KEY}]
"""),
                          environ={})

    assert SECRET_KEY not in errors


def test_bad_password_type_does_not_log_its_value(tmp_path, caplog):
    errors = assert_exits(caplog, "downloaders.0.password",
                          config_path=write_yaml(tmp_path, f"""
version: 1
arrApps:
  - type: radarr
    name: a
    url: localhost:7878
    apiKey: {SECRET_KEY}
downloaders:
  - type: qbittorrent
    name: d
    url: localhost:8080
    password: [{SECRET_PASSWORD}]
"""),
                          environ={})

    assert SECRET_KEY not in errors
    assert SECRET_PASSWORD not in errors


def test_substitution_errors_never_log_resolved_secrets(tmp_path, caplog):
    errors = assert_exits(caplog,
                          "arrApps.1.apiKey", "MISSING_KEY",
                          "downloaders.0.username", "MISSING_USER",
                          "watchers.0.name", "FOO BAR",
                          config_path=write_yaml(tmp_path, """
version: 1
arrApps:
  - type: radarr
    name: a
    url: localhost:7878
    apiKey: ${GOOD_KEY}
  - type: sonarr
    name: b
    url: localhost:8989
    apiKey: prefix-${MISSING_KEY}-suffix
downloaders:
  - type: qbittorrent
    name: d
    url: localhost:8080
    username: ${MISSING_USER}
    password: ${GOOD_PASSWORD}
watchers:
  - name: ${FOO BAR}
"""),
                          environ={"GOOD_KEY": SECRET_KEY, "GOOD_PASSWORD": SECRET_PASSWORD})

    assert SECRET_KEY not in errors
    assert SECRET_PASSWORD not in errors
    assert "prefix-" not in errors
    assert "suffix" not in errors


# --- variable substitution engine -------------------------------------------------------

def substitute(data, environ=None):
    return config._substitute_env_vars(data, environ or {})


def substitution_errors(data, environ=None):
    with pytest.raises(config.ConfigError) as excinfo:
        substitute(data, environ)
    return excinfo.value.messages


def test_substitute_single_reference():
    assert substitute({"apiKey": "${KEY}"}, {"KEY": "abc"}) == {"apiKey": "abc"}


def test_substitute_multiple_references_in_one_string():
    result = substitute({"url": "http://${HOST}:${PORT}/x"}, {"HOST": "arr", "PORT": "7878"})

    assert result == {"url": "http://arr:7878/x"}


def test_substitute_inside_list_and_nested_levels():
    data = {"watchers": [{"tags": ["${TAG}", "plain"], "name": "${NAME}"}]}

    result = substitute(data, {"TAG": "keep", "NAME": "w1"})

    assert result == {"watchers": [{"tags": ["keep", "plain"], "name": "w1"}]}


@pytest.mark.parametrize("environ,expected", [
    ({"VAR": "set"}, "set"),
    ({}, "fallback"),
    ({"VAR": ""}, "fallback"),
    ({"VAR": "   "}, "fallback"),
])
def test_substitute_default_form(environ, expected):
    assert substitute({"a": "${VAR:-fallback}"}, environ) == {"a": expected}


@pytest.mark.parametrize("text,expected", [
    ("$$", "$"),
    ("a$$b", "a$b"),
    ("$${A}", "${A}"),
    ("$", "$"),
    ("$foo", "$foo"),
    ("100$ or 50$", "100$ or 50$"),
])
def test_substitute_escapes_and_bare_dollars(text, expected):
    assert substitute({"a": text}, {"A": "should-not-be-used"}) == {"a": expected}


def test_substitute_leaves_non_string_values_unchanged():
    data = {"port": 9898, "verbose": True, "log": None, "ratio": 1.5}

    assert substitute(data, {}) == data


def test_substitute_never_touches_mapping_keys():
    result = substitute({"${A}": "x"}, {"A": "replaced"})

    assert result == {"${A}": "x"}


@pytest.mark.parametrize("text,expected", [
    ("${MISSING:-}", ""),
    ("${MISSING:-  }", "  "),
])
def test_substitute_uses_supplied_default_verbatim(text, expected):
    """A default the author typed is a value, even when empty; empty ≡ unset is env-only."""
    assert substitute({"a": text}, {}) == {"a": expected}


def test_substitute_preserves_raw_value_whitespace():
    assert substitute({"password": "${P}"}, {"P": "  pw  "}) == {"password": "  pw  "}


@pytest.mark.parametrize("environ", [{}, {"VAR": ""}, {"VAR": "   "}])
def test_substitute_reports_unresolved_variable(environ):
    messages = substitution_errors({"arrApps": [{"apiKey": "${VAR}"}]}, environ)

    assert messages == [
        "arrApps.0.apiKey: undefined variable ${VAR} (set it in the environment, "
        "or supply a default with ${VAR:-...})"
    ]


@pytest.mark.parametrize("spec", ["", "1FOO", "FOO BAR", "FOO-BAR", "FOO\n", "foo.bar", "-"])
def test_substitute_reports_malformed_name(spec):
    messages = substitution_errors({"a": f"${{{spec}}}"}, {"FOO": "x"})

    assert messages == [f"a: malformed variable reference ${{{spec}}}"]


@pytest.mark.parametrize("text", ["${A", "abc${MISSING", "${A:-x"])
def test_substitute_reports_unterminated_reference(text):
    messages = substitution_errors({"arrApps": [{"apiKey": text}]}, {"A": "x"})

    assert messages == ["arrApps.0.apiKey: malformed variable reference ${ (missing closing brace)"]


@pytest.mark.parametrize("environ", [{}, {"A": "x", "OTHER": "zzz"}])
def test_substitute_rejects_default_containing_a_reference(environ):
    """${A:-${OTHER}} parses as default '${OTHER' and would resolve to 'x}' when A is set."""
    messages = substitution_errors({"a": "${A:-${OTHER}}"}, environ)

    assert messages == ["a: malformed variable reference ${A:-${OTHER}"]


def test_substitute_accumulates_every_problem():
    data = {
        "arrApps": [{"apiKey": "${MISSING_ONE}"}],
        "downloaders": [{"password": "${MISSING_TWO}"}],
        "watchers": [{"name": "${FOO BAR}"}],
    }

    messages = substitution_errors(data, {})

    assert len(messages) == 3
    assert any("arrApps.0.apiKey" in m and "${MISSING_ONE}" in m for m in messages)
    assert any("downloaders.0.password" in m and "${MISSING_TWO}" in m for m in messages)
    assert any("watchers.0.name" in m and "${FOO BAR}" in m for m in messages)


def test_substitute_does_not_re_expand_substituted_values():
    assert substitute({"a": "${A}"}, {"A": "${B}", "B": "x"}) == {"a": "${B}"}


def test_substitute_escaped_reference_is_not_flagged_as_unterminated():
    """$${A} legitimately puts a ${ in the output; a post-hoc scan would flag it."""
    assert substitute({"a": "$${A}"}, {}) == {"a": "${A}"}


# --- variable substitution through load_config -------------------------------------------

SUBSTITUTED_YAML = """
    version: 1
    arrApps:
      - type: radarr
        name: anime
        url: ${ARR_HOST}:7878
        apiKey: ${ANIME_RADARR_API_KEY}
    downloaders:
      - type: qbittorrent
        name: box
        url: http://qbit:8080
        username: admin
        password: ${QB_PASSWORD}
    watchers:
      - name: protected
        tags: ["${KEEP_TAG}"]
        action: IGNORE
      - name: catch-all
        action: KEEP
"""

SUBSTITUTED_ENV = {
    "ARR_HOST": "radarr.local",
    "ANIME_RADARR_API_KEY": "anime-key",
    "QB_PASSWORD": "  p@ss  ",
    "KEEP_TAG": "keep",
}


def test_load_config_substitutes_every_section(tmp_path):
    cfg = load(tmp_path, dict(SUBSTITUTED_ENV), SUBSTITUTED_YAML)

    app, = cfg.arr_apps
    assert (app.url, app.api_key) == ("http://radarr.local:7878", "anime-key")
    assert cfg.downloader.password == "  p@ss  "
    assert cfg.watchers[0].tags == ("keep",)


def test_unquoted_reference_in_flow_collection_is_invalid_yaml(tmp_path, caplog):
    """`{` is a YAML flow indicator: tags: [${TAG}] never reaches substitution."""
    errors = assert_exits(caplog, "invalid YAML",
                          config_path=write_yaml(tmp_path, """
                              version: 1
                              watchers:
                                - name: w
                                  tags: [${TAG}]
                          """),
                          environ={"RADARR_URL": "http://a", "RADARR_API_KEY": "k", "TAG": "keep"})

    assert "undefined variable" not in errors


def test_substituted_values_coerce_to_non_string_fields(tmp_path):
    cfg = load(tmp_path, {
        "RADARR_URL": "http://a", "RADARR_API_KEY": "k",
        "HEALTH_PORT": "9898", "VERBOSE_FLAG": "true",
        "TIMEOUT": "1h", "INTERVAL": "5m",
    }, """
        version: 1
        runInterval: ${INTERVAL}
        log:
          verbose: ${VERBOSE_FLAG}
        healthCheck:
          port: ${HEALTH_PORT}
        watchers:
          - name: only
            stalledTimeout: ${TIMEOUT}
            action: KEEP
    """)

    assert cfg.health_port == 9898
    assert cfg.verbose is True
    assert cfg.watchers[0].stalled_timeout == 3600
    assert cfg.run_interval == 300


def test_substitution_never_consults_os_environ(tmp_path, caplog, monkeypatch):
    monkeypatch.setenv("SECRET", "leaked")

    errors = assert_exits(caplog, "arrApps.0.apiKey", "${SECRET}",
                          config_path=write_yaml(tmp_path, """
                              version: 1
                              arrApps:
                                - type: radarr
                                  name: a
                                  url: localhost:7878
                                  apiKey: ${SECRET}
                          """),
                          environ={})

    assert "leaked" not in errors


@pytest.mark.parametrize("value", ['a: b #c "d"', "line1\nline2", "key: [not, a, list]"])
def test_substituted_value_cannot_corrupt_document_structure(tmp_path, value):
    cfg = load(tmp_path, {"KEY": value}, """
        version: 1
        arrApps:
          - type: radarr
            name: a
            url: localhost:7878
            apiKey: ${KEY}
    """)

    assert cfg.arr_apps[0].api_key == value


def test_undefined_variable_exits_naming_variable_and_path(tmp_path, caplog):
    assert_exits(caplog, "arrApps.0.apiKey", "${ANIME_RADARR_API_KEY}", "undefined variable",
                 config_path=write_yaml(tmp_path, SUBSTITUTED_YAML),
                 environ=dict(SUBSTITUTED_ENV, ANIME_RADARR_API_KEY=""))


def test_every_bad_reference_across_sections_is_logged(tmp_path, caplog):
    errors = assert_exits(caplog, "arrApps.0.apiKey", "downloaders.0.password", "watchers.0.tags.0",
                          config_path=write_yaml(tmp_path, SUBSTITUTED_YAML),
                          environ={"ARR_HOST": "radarr.local"})

    assert errors.count("|") >= 2


def test_unterminated_reference_is_not_accepted_as_a_literal_key(tmp_path, caplog):
    errors = assert_exits(caplog, "arrApps.0.apiKey", "malformed variable reference",
                          config_path=write_yaml(tmp_path, """
                              version: 1
                              arrApps:
                                - type: radarr
                                  name: a
                                  url: localhost:7878
                                  apiKey: ${RADARR_API_KEY
                          """),
                          environ={"RADARR_API_KEY": "k"})

    assert "missing closing brace" in errors


@pytest.mark.parametrize("api_key", ['""', '"${MISSING:-}"'])
def test_empty_api_key_is_rejected(tmp_path, caplog, api_key):
    """${MISSING:-} substitutes cleanly to ''; the rejection comes from the apiKey validator."""
    errors = assert_exits(caplog, "arrApps.0.apiKey",
                          config_path=write_yaml(tmp_path, f"""
                              version: 1
                              arrApps:
                                - type: radarr
                                  name: a
                                  url: localhost:7878
                                  apiKey: {api_key}
                          """),
                          environ={})

    assert "undefined variable" not in errors


def test_empty_password_stays_expressible(tmp_path):
    cfg = load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "k"}, """
        version: 1
        downloaders:
          - type: qbittorrent
            name: box
            url: http://qbit:8080
            username: admin
            password: "${QB_PASSWORD:-}"
    """)

    assert cfg.downloader.password == ""


def test_config_without_references_is_unaffected(tmp_path):
    """FULL_YAML contains no ${...}; substitution must leave it byte-for-byte equivalent."""
    cfg = load(tmp_path, {"RADARR_API_KEY": "should-be-ignored"}, FULL_YAML)

    assert [(a.name, a.url, a.api_key) for a in cfg.arr_apps] == [
        ("anime", "http://localhost:7878", "xxxyyyzzz"),
        ("shows", "https://sonarr.example.com", "aaabbbccc"),
    ]
    assert cfg.downloader == config.Downloader("default", "http://localhost:9999", "admin", "abcd")
    assert cfg.watchers[0].tags == ("keep", "private")
    assert cfg.run_interval == 300


def test_env_only_values_are_never_interpolated(tmp_path):
    cfg = load(tmp_path, {"RADARR_URL": "http://a", "RADARR_API_KEY": "${NOT_A_REFERENCE}",
                          "QBITTORRENT_URL": "http://q", "QBITTORRENT_PASSWORD": "${MISSING"})

    assert cfg.arr_apps[0].api_key == "${NOT_A_REFERENCE}"
    assert cfg.downloader.password == "${MISSING"
