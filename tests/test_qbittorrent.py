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
INFO_URL = f"{QBIT}/api/v2/torrents/info"


def gets():
    return [c for c in responses.calls if c.request.method == "GET"]


def posts():
    return [c for c in responses.calls if c.request.method == "POST"]


@pytest.fixture
def client(load_main):
    """A QbitClient built from a freshly reloaded main (no env needed)."""
    m = load_main({})
    return m.QbitClient(QBIT, "admin", "pw")


@responses.activate
def test_login_ok(client):
    responses.post(LOGIN_URL, body="Ok.")

    assert client.login() is True
    assert "username=admin" in posts()[0].request.body
    assert "password=pw" in posts()[0].request.body


@responses.activate
def test_login_empty_body_auth_bypass(client):
    responses.post(LOGIN_URL, body="", status=204)

    assert client.login() is True


@responses.activate
def test_login_fails_body(client):
    responses.post(LOGIN_URL, body="Fails.")

    assert client.login() is False
    assert client.cookies is None


@responses.activate
def test_login_connection_error(client):
    responses.post(LOGIN_URL, body=requests.exceptions.ConnectionError())

    assert client.login() is False


@responses.activate
def test_get_tags_happy_path(client):
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, json=[{"tags": "a, b"}])

    assert client.get_tags("ABC123HASH") == ["a", "b"]
    assert all("abc123hash" in call.request.url for call in gets())


@responses.activate
def test_get_tags_empty_tag_string(client):
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, json=[{"tags": ""}])

    assert client.get_tags("ABC123HASH") == []


@responses.activate
def test_get_tags_logs_in_once(client):
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, json=[{"tags": "slow"}])

    assert client.get_tags("ABC123HASH") == ["slow"]
    assert client.get_tags("ABC123HASH") == ["slow"]
    assert len(posts()) == 1


@responses.activate
def test_get_tags_relogin_on_403(client):
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, status=403)
    responses.get(INFO_URL, json=[{"tags": "slow"}])

    assert client.get_tags("ABC123HASH") == ["slow"]
    assert len(posts()) == 2


@responses.activate
def test_get_tags_relogin_failure_returns_none(client):
    responses.post(LOGIN_URL, body="Ok.")
    responses.post(LOGIN_URL, body="Fails.")
    responses.get(INFO_URL, status=403)

    assert client.get_tags("ABC123HASH") is None


@responses.activate
def test_get_tags_login_failure_returns_none(client):
    responses.post(LOGIN_URL, body="Fails.")

    assert client.get_tags("ABC123HASH") is None
    assert gets() == []


@responses.activate
def test_get_tags_non_ok_returns_none(client):
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, status=500)

    assert client.get_tags("ABC123HASH") is None


@responses.activate
def test_get_tags_request_error_returns_none(client):
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, body=requests.exceptions.ConnectionError())

    assert client.get_tags("ABC123HASH") is None


@responses.activate
def test_get_tags_unknown_hash_returns_empty_list(client):
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, json=[])

    # A successful lookup for a hash qBittorrent doesn't know means "no tags", not "failure".
    assert client.get_tags("ABC123HASH") == []


@responses.activate
def test_get_tags_without_hash_makes_no_calls(client):
    assert client.get_tags(None) is None
    assert len(responses.calls) == 0


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
    responses.get(INFO_URL, json=[{"tags": "slow"}])

    assert m.should_ignore_download(queue_item()) is True


@responses.activate
def test_should_ignore_tag_case_sensitive_no_match(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, json=[{"tags": "Slow"}])

    assert m.should_ignore_download(queue_item()) is False


@responses.activate
def test_should_ignore_lookup_failure_fails_open(load_main):
    m = load_main(QBIT_ENV)
    responses.post(LOGIN_URL, body="Ok.")
    responses.get(INFO_URL, status=500)

    # Fail-open is intended: qBittorrent being unreachable must not disable the handler.
    assert m.should_ignore_download(queue_item()) is False
