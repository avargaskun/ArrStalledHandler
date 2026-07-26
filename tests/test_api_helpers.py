import json
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
def test_delete_api_sends_params_and_swallows_errors(load_main):
    m = load_main({})
    responses.delete("http://arr/api/v3/queue/77", json={})

    m.delete_api("http://arr/api/v3/queue/77", HEADERS, {"blocklist": "true", "skipRedownload": "false"})

    query = parse_qs(urlparse(responses.calls[0].request.url).query)
    assert query["blocklist"] == ["true"]
    assert query["skipRedownload"] == ["false"]

    responses.delete("http://arr/api/v3/queue/77", status=500)
    m.delete_api("http://arr/api/v3/queue/77", HEADERS, {"blocklist": "true"})


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
    responses.get(URL, json=queue_page(records, total=99))
    responses.get(URL, json=queue_page([], total=99))

    assert m.query_api_paginated(URL, HEADERS) == records
    assert len(responses.calls) == 2


@responses.activate
def test_paginated_stops_on_none_response(load_main):
    m = load_main({})
    records = [{"id": 1}]
    responses.get(URL, json=queue_page(records, total=99))
    responses.get(URL, status=500)

    assert m.query_api_paginated(URL, HEADERS) == records


@responses.activate
def test_paginated_stops_on_malformed_response(load_main):
    m = load_main({})
    responses.get(URL, json={})

    assert m.query_api_paginated(URL, HEADERS) == []
