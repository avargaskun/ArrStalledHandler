import logging
from datetime import datetime, timedelta, timezone


def test_initialize_creates_table(load_main):
    m = load_main({})
    m.initialize_database()

    first_detected = datetime.now(timezone.utc)
    assert m.add_stalled_download_to_db("1", first_detected, "Radarr0") is True
    assert m.get_stalled_downloads_from_db("Radarr0") == {"1": first_detected}


def test_initialize_creates_table_when_timeout_zero(load_main):
    m = load_main({"STALLED_TIMEOUT": "0"})
    m.initialize_database()

    assert m.get_stalled_downloads_from_db("Radarr0") == {}


def test_add_returns_true_then_false_on_duplicate(load_main):
    m = load_main({})
    m.initialize_database()

    original = datetime.now(timezone.utc) - timedelta(seconds=500)
    assert m.add_stalled_download_to_db("1", original, "Radarr0") is True
    assert m.add_stalled_download_to_db("1", datetime.now(timezone.utc), "Radarr0") is False
    assert m.get_stalled_downloads_from_db("Radarr0")["1"] == original


def test_get_filters_by_service(load_main):
    m = load_main({})
    m.initialize_database()

    now = datetime.now(timezone.utc)
    m.add_stalled_download_to_db("1", now, "Radarr0")
    m.add_stalled_download_to_db("2", now, "Sonarr0")

    assert list(m.get_stalled_downloads_from_db("Radarr0")) == ["1"]
    assert list(m.get_stalled_downloads_from_db("Sonarr0")) == ["2"]


def test_same_download_id_across_services(load_main):
    m = load_main({})
    m.initialize_database()

    radarr_time = datetime.now(timezone.utc) - timedelta(seconds=100)
    sonarr_time = datetime.now(timezone.utc)
    assert m.add_stalled_download_to_db("7", radarr_time, "Radarr0") is True
    assert m.add_stalled_download_to_db("7", sonarr_time, "Sonarr0") is True

    assert m.get_stalled_downloads_from_db("Radarr0") == {"7": radarr_time}
    assert m.get_stalled_downloads_from_db("Sonarr0") == {"7": sonarr_time}


def test_remove_deletes_only_matching_service(load_main):
    m = load_main({})
    m.initialize_database()

    now = datetime.now(timezone.utc)
    m.add_stalled_download_to_db("7", now, "Radarr0")
    m.add_stalled_download_to_db("7", now, "Sonarr0")

    m.remove_stalled_download_from_db("7", "Radarr0")

    assert m.get_stalled_downloads_from_db("Radarr0") == {}
    assert m.get_stalled_downloads_from_db("Sonarr0") == {"7": now}


def test_add_and_remove_accept_int_download_id(load_main):
    m = load_main({})
    m.initialize_database()

    m.add_stalled_download_to_db(7, datetime.now(timezone.utc), "Radarr0")
    m.remove_stalled_download_from_db(7, "Radarr0")

    assert m.get_stalled_downloads_from_db("Radarr0") == {}


def test_db_file_parameter_isolates_databases(load_main, tmp_path):
    m = load_main({})
    other = str(tmp_path / "other.db")
    m.initialize_database()
    m.initialize_database(db_file=other)

    now = datetime.now(timezone.utc)
    m.add_stalled_download_to_db("1", now, "Radarr0", db_file=other)

    assert m.get_stalled_downloads_from_db("Radarr0") == {}
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=other) == {"1": now}

    m.remove_stalled_download_from_db("1", "Radarr0", db_file=other)
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=other) == {}


def test_prune_removes_unconfigured_services(load_main, tmp_path, caplog):
    m = load_main({})
    db = str(tmp_path / "prune.db")
    m.initialize_database(db_file=db)

    now = datetime.now(timezone.utc)
    m.add_stalled_download_to_db("1", now, "Radarr0", db_file=db)
    m.add_stalled_download_to_db("2", now, "Sonarr0", db_file=db)
    m.add_stalled_download_to_db("3", now, "lidarr0", db_file=db)

    with caplog.at_level(logging.INFO):
        assert m.prune_orphaned_services(db, ["Radarr0", "Sonarr0"]) == 1

    assert list(m.get_stalled_downloads_from_db("Radarr0", db_file=db)) == ["1"]
    assert list(m.get_stalled_downloads_from_db("Sonarr0", db_file=db)) == ["2"]
    assert m.get_stalled_downloads_from_db("lidarr0", db_file=db) == {}
    assert "Pruned 1" in caplog.text


def test_prune_keeps_everything_when_all_configured(load_main, tmp_path, caplog):
    m = load_main({})
    db = str(tmp_path / "prune.db")
    m.initialize_database(db_file=db)
    m.add_stalled_download_to_db("1", datetime.now(timezone.utc), "Radarr0", db_file=db)

    with caplog.at_level(logging.INFO):
        assert m.prune_orphaned_services(db, ["Radarr0"]) == 0

    assert list(m.get_stalled_downloads_from_db("Radarr0", db_file=db)) == ["1"]
    assert "Pruned" not in caplog.text


def test_prune_with_no_configured_names_clears_table(load_main, tmp_path):
    m = load_main({})
    db = str(tmp_path / "prune.db")
    m.initialize_database(db_file=db)
    m.add_stalled_download_to_db("1", datetime.now(timezone.utc), "Radarr0", db_file=db)

    assert m.prune_orphaned_services(db, []) == 1
    assert m.get_stalled_downloads_from_db("Radarr0", db_file=db) == {}
