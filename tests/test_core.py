"""Request building, sending, assertions, captures and helpers (no GUI)."""
import json

import requests

import curlpypro as cp


def send(req, env=None, jar=None, timeout=10):
    call = cp.prepare_request(req, env or {}, timeout)
    resp, _elapsed = cp.execute_call(call, jar)
    return resp


def req(**kw):
    r = cp._empty_request("t")
    r.update(kw)
    return r


# ---------- environment substitution ----------
def test_apply_env_basic():
    assert cp.apply_env("{{a}}/{{b}}/{{missing}}", {"a": 1, "b": "x"}) == "1/x/{{missing}}"


# ---------- sending ----------
def test_get_with_params_and_headers(server):
    r = send(req(url=server + "/echo?x=1", headers="X-Test: {{v}}",
                 params=[{"enabled": True, "key": "y", "value": "{{v}}"},
                         {"enabled": False, "key": "off", "value": "1"}]), {"v": "hello"})
    data = r.json()
    assert data["query"] == {"x": "1", "y": "hello"}
    assert data["headers"]["X-Test"] == "hello"


def test_json_body(server):
    r = send(req(method="POST", url=server + "/echo", body_type="JSON", body='{"a": "{{v}}"}'), {"v": "1"})
    data = r.json()
    assert json.loads(data["body"]) == {"a": "1"}
    assert data["headers"]["Content-Type"] == "application/json"


def test_raw_body(server):
    r = send(req(method="POST", url=server + "/echo", body="plain text", headers="Content-Type: text/plain"))
    assert r.json()["body"] == "plain text"


def test_form_body(server):
    r = send(req(method="POST", url=server + "/echo", body_type="Form Data", body="a=1&b=two words"))
    data = r.json()
    assert "application/x-www-form-urlencoded" in data["headers"]["Content-Type"]
    assert dict(p.split("=") for p in data["body"].split("&")) == {"a": "1", "b": "two+words"}


def test_multipart_upload(server, tmp_path):
    f = tmp_path / "pic.txt"
    f.write_text("file-content")
    r = send(req(method="POST", url=server + "/echo", body_type="Form Data", body="note=hi",
                 attachments=[{"field": "file", "path": str(f), "filename": "pic.txt", "mime": "text/plain"}]))
    data = r.json()
    assert data["headers"]["Content-Type"].startswith("multipart/form-data")
    assert "file-content" in data["body"] and 'name="note"' in data["body"]


def test_graphql_body(server):
    r = send(req(method="POST", url=server + "/graphql", body_type="GraphQL",
                 body="query { user(id: $id) { id } }", graphql_variables='{"id": "{{uid}}"}'), {"uid": "5"})
    assert r.json()["data"]["echo"]["variables"] == {"id": "5"}


def test_invalid_json_body_raises():
    try:
        cp.prepare_request(req(method="POST", url="http://x", body_type="JSON", body="{bad"), {})
    except cp.RequestBuildError as e:
        assert e.title == "Invalid JSON"
    else:
        raise AssertionError("expected RequestBuildError")


def test_missing_url_raises():
    try:
        cp.prepare_request(req(url="  "), {})
    except cp.RequestBuildError:
        pass
    else:
        raise AssertionError("expected RequestBuildError")


def test_bearer_basic_apikey_auth(server):
    r = send(req(url=server + "/me", auth={"type": "Bearer Token", "token": "{{T}}"}), {"T": "tok-123"})
    assert r.status_code == 200
    r = send(req(url=server + "/basic", auth={"type": "Basic Auth", "username": "ann", "password": "pw"}))
    assert r.status_code == 200
    r = send(req(url=server + "/echo", auth={"type": "API Key", "key": "X-Key", "value": "k1", "add_to": "Header"}))
    assert r.json()["headers"]["X-Key"] == "k1"
    r = send(req(url=server + "/echo", auth={"type": "API Key", "key": "api_key", "value": "k2", "add_to": "Query Params"}))
    assert r.json()["query"]["api_key"] == "k2"


def test_oauth_client_credentials(server):
    token, expires = cp.fetch_oauth2_token({"grant_type": "Client Credentials", "token_url": server + "/oauth/token",
                                            "client_id": "c", "client_secret": "s"})
    assert token == "oauth-client_credentials" and expires == 3600


def test_redirects(server):
    r = send(req(url=server + "/redirect"))
    assert r.status_code == 200 and r.json()["query"] == {"redirected": "1"}
    r = send(req(url=server + "/redirect", advanced={"follow_redirects": False}))
    assert r.status_code == 302


def test_cookie_jar_shared(server):
    jar = requests.cookies.RequestsCookieJar()
    send(req(url=server + "/set-cookie"), jar=jar)
    assert send(req(url=server + "/read-cookie"), jar=jar).json()["cookie"] == "session=abc"
    restored = cp.list_to_cookiejar(cp.cookiejar_to_list(jar))
    assert send(req(url=server + "/read-cookie"), jar=restored).json()["cookie"] == "session=abc"


def test_retries(server):
    r = send(req(url=server + "/flaky?key=retry&fail=2",
                 advanced={"retry_total": 3, "retry_backoff": 0, "retry_statuses": "503"}))
    assert r.status_code == 200 and r.json()["try"] == 3


def test_timeout(server):
    try:
        send(req(url=server + "/slow?s=2"), timeout=0.3)
    except requests.exceptions.Timeout:
        pass
    else:
        raise AssertionError("expected a timeout")


# ---------- assertions and captures ----------
def test_json_path():
    data = {"a": {"items": [{"id": 1}, {"id": 2}]}, "k.dot": 3}
    assert cp.json_path_get(data, "$.a.items[1].id") == (True, 2)
    assert cp.json_path_get(data, "$.a.items[-1].id") == (True, 2)
    assert cp.json_path_get(data, "$.a.items.length") == (True, 2)
    assert cp.json_path_get(data, '$["k.dot"]') == (True, 3)
    assert cp.json_path_get(data, "$.nope")[0] is False


def test_assertions_and_captures(server):
    resp = send(req(method="POST", url=server + "/login", body_type="JSON",
                    body='{"user": "ann", "password": "pw"}'))
    tests = [
        {"source": "Status code", "operator": "equals", "expected": "200"},
        {"source": "Status code", "operator": "one of", "expected": "200, 201"},
        {"source": "JSON path", "property": "$.user.id", "operator": "equals", "expected": "7"},
        {"source": "JSON path", "property": "$.token", "operator": "matches regex", "expected": "^tok-"},
        {"source": "Header", "property": "X-Request-Id", "operator": "exists"},
        {"source": "Response time (ms)", "operator": "<", "expected": "5000"},
        {"source": "Body", "operator": "contains", "expected": "ann"},
        {"source": "Body size (bytes)", "operator": ">", "expected": "10"},
        {"source": "JSON path", "property": "$.missing", "operator": "not exists"},
        {"source": "JSON path", "property": "$.user.name", "operator": "equals", "expected": "{{expected_name}}"},
    ]
    results = cp.evaluate_assertions(tests, resp, 12, {"expected_name": "ann"})
    assert all(r["passed"] for r in results), results
    failing = cp.evaluate_assertions([{"source": "Status code", "operator": "equals", "expected": "201"}], resp, 1, {})
    assert failing[0]["passed"] is False and "200" in failing[0]["message"]

    values, problems = cp.extract_captures([
        {"variable": "TOKEN", "source": "JSON path", "expression": "$.token"},
        {"variable": "RID", "source": "Header", "expression": "X-Request-Id"},
        {"variable": "CODE", "source": "Status code"},
        {"variable": "NAME", "source": "Body regex", "expression": r'"name": "(\w+)"'},
        {"variable": "NOPE", "source": "JSON path", "expression": "$.nope"},
    ], resp)
    assert values == {"TOKEN": "tok-123", "RID": "req-1", "CODE": "200", "NAME": "ann"}
    assert len(problems) == 1


# ---------- curl import ----------
def test_parse_curl():
    p = cp.parse_curl_command(
        "curl -X POST 'https://api.example.com/v1/items?x=1' -H 'Content-Type: application/json' "
        "-H \"Authorization: Bearer abc\" --data-raw '{\"a\": 1}' -u ann:pw --compressed")
    assert p["method"] == "POST"
    assert p["url"] == "https://api.example.com/v1/items?x=1"
    assert ("Content-Type", "application/json") in p["headers"]
    assert p["data"] == '{"a": 1}' and p["user"] == "ann:pw"


def test_parse_curl_multiline_and_form():
    p = cp.parse_curl_command("curl https://x.io/up \\\n  -F 'file=@/tmp/a.png;type=image/png' \\\n  -F name=bob")
    assert p["is_form"] and p["form"] == ["file=@/tmp/a.png;type=image/png", "name=bob"]
    kind, payload = cp.parse_curl_form_value(p["form"][0])
    assert kind == "file" and payload["mime"] == "image/png"


# ---------- misc helpers ----------
def test_proxy_helpers():
    assert cp.normalize_proxy_url("PROXY proxy.corp:8080; DIRECT") == "http://proxy.corp:8080"
    assert cp.normalize_proxy_url("DIRECT") == ""
    assert cp.build_proxies("host:1", "<local>;*.corp") == {
        "http": "http://host:1", "https": "http://host:1", "no_proxy": "*.corp"}


def test_diff_html():
    html, added, removed = cp.diff_html(cp.normalize_for_diff('{"b":1,"a":2}'),
                                        cp.normalize_for_diff('{"a":2,"b":3}'), "old", "new")
    assert added == 1 and removed == 1 and "<pre" in html


def test_status_code_list():
    assert cp.parse_status_code_list("500, 502 abc 99 503") == [500, 502, 503]


# ---------- variables ----------
def test_variable_names_with_dots_and_dashes():
    env = {"base-url": "http://h", "api.key": "k", "spaced": "s"}
    assert cp.apply_env("{{base-url}}/x?k={{api.key}}&s={{ spaced }}", env) == "http://h/x?k=k&s=s"


def test_dynamic_variables():
    import re
    out = cp.apply_env("{{$guid}} {{$timestamp}} {{$randomInt}} {{$isoTimestamp}} {{$nope}}", {})
    guid, ts, rnd, iso, nope = out.split(" ")
    assert re.fullmatch(r"[0-9a-f-]{36}", guid)
    assert ts.isdigit() and rnd.isdigit() and iso.endswith("Z")
    assert nope == "{{$nope}}"
    # An environment value wins over the built-in.
    assert cp.apply_env("{{$guid}}", {"$guid": "fixed"}) == "fixed"
    assert cp.apply_env("{{$guid}}", {}) != cp.apply_env("{{$guid}}", {})


def test_find_unresolved():
    assert cp.find_unresolved("{{a}} {{b}} {{a}} {{$guid}} {{$bad}}", {"b": 1}) == ["a", "$bad"]


# ---------- binary body ----------
def test_binary_body(server, tmp_path):
    f = tmp_path / "blob.png"
    f.write_bytes(bytes(range(256)))
    r = send(req(method="PUT", url=server + "/echo", body_type="Binary", binary_file=str(f)))
    data = r.json()
    import base64
    assert base64.b64decode(data["body_b64"]) == bytes(range(256))
    assert data["headers"]["Content-Type"] == "image/png"


def test_binary_body_missing_file():
    try:
        cp.prepare_request(req(method="PUT", url="http://x", body_type="Binary", binary_file="/no/such/file"), {})
    except cp.RequestBuildError as e:
        assert e.critical
    else:
        raise AssertionError("expected RequestBuildError")


# ---------- request ids ----------
def test_request_ids():
    colls = {"A": [{"name": "x"}], "B": [{"name": "y", "id": "abc"}]}
    rid = cp.ensure_request_id(colls["A"][0])
    assert cp.ensure_request_id(colls["A"][0]) == rid
    assert cp.find_request_by_id(colls, rid) == ("A", 0)
    assert cp.find_request_by_id(colls, "abc") == ("B", 0)
    assert cp.find_request_by_id(colls, "zzz") is None
