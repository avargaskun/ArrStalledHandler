import pytest

import config
from config import QueueItemDisposition as Disposition


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
