import pytest
import requests
import responses

from conftest import queue_item

QBIT = "http://qbit:8080"
QBIT_ENV = {
    "QBITTORRENT_URL": QBIT,
    "QBITTORRENT_USERNAME": "admin",
    "QBITTORRENT_PASSWORD": "pw",
    "IGNORE_TORRENT_TAGS": "slow,manual",
}

LOGIN_URL = f"{QBIT}/api/v2/auth/login"
PROPS_URL = f"{QBIT}/api/v2/torrents/properties"
INFO_URL = f"{QBIT}/api/v2/torrents/info"


def gets():
    return [c for c in responses.calls if c.request.method == "GET"]


@responses.activate
def test_login_no_url_returns_false(load_main):
    m = load_main({})

    assert m.qbittorrent_login() is False
    assert len(responses.calls) == 0


@responses.activate
def test_login_ok(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body="Ok.")

    assert m.qbittorrent_login() is True
    assert "username=admin" in responses.calls[0].request.body
    assert "password=pw" in responses.calls[0].request.body


@responses.activate
def test_login_empty_body_auth_bypass(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body="", status=204)

    assert m.qbittorrent_login() is True


@responses.activate
def test_login_fails_body(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body="Fails.")

    assert m.qbittorrent_login() is False


@responses.activate
def test_login_connection_error(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body=requests.exceptions.ConnectionError())

    assert m.qbittorrent_login() is False


@responses.activate
def test_torrent_info_happy_path(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(PROPS_URL, json={"name": "release"})
    responses.get(INFO_URL, json=[{"tags": "a, b"}])
    responses.get(INFO_URL, json=[{"tags": ""}])

    info = m.get_torrent_info_by_hash("ABC123HASH")

    assert info["tags"] == ["a", "b"]
    assert all("abc123hash" in call.request.url for call in gets())
    assert m.get_torrent_info_by_hash("ABC123HASH")["tags"] == []


@responses.activate
def test_torrent_info_relogin_on_403(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(PROPS_URL, status=403)
    responses.get(PROPS_URL, json={"name": "release"})
    responses.get(INFO_URL, json=[{"tags": "slow"}])

    info = m.get_torrent_info_by_hash("ABC123HASH")

    assert info["tags"] == ["slow"]
    assert len([c for c in responses.calls if c.request.method == "POST"]) == 2


@responses.activate
def test_torrent_info_non_ok_returns_false(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(PROPS_URL, status=404)

    # D4: this path returns False where other failures return None; callers only truthiness-check.
    assert m.get_torrent_info_by_hash("ABC123HASH") is False


@pytest.mark.parametrize("env", [{}, {"QBITTORRENT_URL": QBIT}])
@responses.activate
def test_should_ignore_without_qbit_config(load_main, env):
    m = load_main(env)

    assert m.should_ignore_download(queue_item()) is False
    assert len(responses.calls) == 0


@responses.activate
def test_should_ignore_non_qbittorrent_client(load_main):
    m = load_main(QBIT_ENV)

    assert m.should_ignore_download(queue_item(download_client="SABnzbd")) is False
    assert len(responses.calls) == 0


@responses.activate
def test_should_ignore_missing_download_id(load_main):
    m = load_main(QBIT_ENV)

    assert m.should_ignore_download(queue_item(download_id=None)) is False
    assert len(responses.calls) == 0


@responses.activate
def test_should_ignore_tag_match(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(PROPS_URL, json={"name": "release"})
    responses.get(INFO_URL, json=[{"tags": "slow"}])

    assert m.should_ignore_download(queue_item()) is True


@responses.activate
def test_should_ignore_tag_case_sensitive_no_match(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(PROPS_URL, json={"name": "release"})
    responses.get(INFO_URL, json=[{"tags": "Slow"}])

    assert m.should_ignore_download(queue_item()) is False


@responses.activate
def test_should_ignore_lookup_failure_fails_open(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(PROPS_URL, status=500)

    # Fail-open is intended: qBittorrent being unreachable must not disable the handler.
    assert m.should_ignore_download(queue_item()) is False
