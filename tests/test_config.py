import pytest


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
