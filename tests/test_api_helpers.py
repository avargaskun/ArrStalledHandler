import json
import logging
from urllib.parse import parse_qs, urlparse

import requests
import responses

from conftest import queue_page

URL = "http://arr/api/v3/queue"
HEADERS = {"X-Api-Key": "k"}


@responses.activate
def test_query_api_returns_json(load_main):
    m = load_main({})
    responses.get(URL, json={"a": 1})

    assert m.query_api(URL, HEADERS) == {"a": 1}
    assert responses.calls[0].request.headers["X-Api-Key"] == "k"


@responses.activate
def test_query_api_http_error_returns_none(load_main):
    m = load_main({})
    responses.get(URL, status=500)

    assert m.query_api(URL, HEADERS) is None


@responses.activate
def test_query_api_connection_error_returns_none(load_main):
    m = load_main({})
    responses.get(URL, body=requests.exceptions.ConnectionError())

    assert m.query_api(URL, HEADERS) is None


@responses.activate
def test_post_api_sends_json_and_swallows_errors(load_main):
    m = load_main({})
    responses.post("http://arr/api/v3/command", json={})

    m.post_api("http://arr/api/v3/command", HEADERS, {"name": "MoviesSearch", "movieIds": [7]})

    assert json.loads(responses.calls[0].request.body) == {"name": "MoviesSearch", "movieIds": [7]}

    responses.post("http://arr/api/v3/command", status=500)
    m.post_api("http://arr/api/v3/command", HEADERS, {"name": "MoviesSearch"})


@responses.activate
def test_delete_api_returns_status_and_sends_params(load_main):
    m = load_main({})
    responses.delete("http://arr/api/v3/queue/77", json={})

    status = m.delete_api("http://arr/api/v3/queue/77", HEADERS, {"blocklist": "true", "skipRedownload": "false"})

    assert status == 200
    query = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert query["blocklist"] == ["true"]
    assert query["skipRedownload"] == ["false"]


@responses.activate
def test_delete_api_returns_404_without_logging_error(load_main, caplog):
    m = load_main({})
    responses.delete("http://arr/api/v3/queue/77", status=404)

    assert m.delete_api("http://arr/api/v3/queue/77", HEADERS) == 404
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


@responses.activate
def test_delete_api_returns_500_without_logging_error(load_main, caplog):
    m = load_main({})
    responses.delete("http://arr/api/v3/queue/77", status=500)

    assert m.delete_api("http://arr/api/v3/queue/77", HEADERS) == 500
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


@responses.activate
def test_delete_api_connection_error_returns_none_and_logs(load_main, caplog):
    m = load_main({})
    responses.delete("http://arr/api/v3/queue/77", body=requests.exceptions.ConnectionError("boom"))

    assert m.delete_api("http://arr/api/v3/queue/77", HEADERS) is None
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


@responses.activate
def test_paginated_single_page(load_main):
    m = load_main({})
    records = [{"id": i} for i in range(10)]
    responses.get(URL, json=queue_page(records))

    assert m.query_api_paginated(URL, HEADERS) == records
    assert len(responses.calls) == 1


@responses.activate
def test_paginated_multiple_pages(load_main):
    m = load_main({})
    pages = [[{"id": i} for i in range(50)],
             [{"id": i} for i in range(50, 100)],
             [{"id": i} for i in range(100, 120)]]
    for page in pages:
        responses.get(URL, json=queue_page(page, total=120))

    result = m.query_api_paginated(URL, HEADERS)

    assert len(result) == 120
    assert len(responses.calls) == 3
    assert [parse_qs(urlparse(c.request.url).query)["page"][0] for c in responses.calls] == ["1", "2", "3"]


@responses.activate
def test_paginated_stops_on_empty_page(load_main):
    m = load_main({})
    records = [{"id": 1}]
    responses.get(URL, json=queue_page(records, total=0))
    responses.get(URL, json=queue_page([], total=0))

    assert m.query_api_paginated(URL, HEADERS) == records
    assert len(responses.calls) == 2


@responses.activate
def test_paginated_returns_none_on_short_delivery(load_main):
    m = load_main({})
    records = [{"id": 1}]
    responses.get(URL, json=queue_page(records, total=99))
    responses.get(URL, json=queue_page([], total=99))

    assert m.query_api_paginated(URL, HEADERS) is None


@responses.activate
def test_paginated_returns_empty_list_for_empty_queue(load_main):
    m = load_main({})
    responses.get(URL, json=queue_page([], total=0))

    result = m.query_api_paginated(URL, HEADERS)

    assert result == []
    assert result is not None


@responses.activate
def test_paginated_returns_none_on_page_failure(load_main):
    m = load_main({})
    records = [{"id": 1}]
    responses.get(URL, json=queue_page(records, total=99))
    responses.get(URL, status=500)

    assert m.query_api_paginated(URL, HEADERS) is None


@responses.activate
def test_paginated_returns_none_on_malformed_response(load_main):
    m = load_main({})
    responses.get(URL, json={})

    assert m.query_api_paginated(URL, HEADERS) is None
