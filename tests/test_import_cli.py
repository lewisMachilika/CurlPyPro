"""Postman/OpenAPI import and the `curlpypro run` command-line runner."""
import io
import json

import curlpypro as cp


POSTMAN = {
    "info": {"name": "Shop", "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
    "variable": [{"key": "baseUrl", "value": "http://example.invalid"}],
    "auth": {"type": "bearer", "bearer": [{"key": "token", "value": "{{TOKEN}}"}]},
    "item": [
        {"name": "Auth", "item": [
            {"name": "Login", "request": {
                "method": "POST", "url": "{{baseUrl}}/login",
                "header": [{"key": "Content-Type", "value": "application/json"}],
                "body": {"mode": "raw", "raw": "{\"user\": \"ann\", \"password\": \"pw\"}",
                         "options": {"raw": {"language": "json"}}},
                "auth": {"type": "noauth"}},
             "event": [{"listen": "test", "script": {"exec": ["pm.response.to.have.status(200);"]}}]},
        ]},
        {"name": "Get item", "request": {
            "method": "GET",
            "url": {"raw": "{{baseUrl}}/items/:id?full=1", "host": ["{{baseUrl}}"], "path": ["items", ":id"],
                    "query": [{"key": "full", "value": "1"}], "variable": [{"key": "id", "value": "42"}]}}},
    ],
}

OPENAPI = {
    "openapi": "3.0.0",
    "info": {"title": "Pets"},
    "servers": [{"url": "https://{env}.pets.io/v1", "variables": {"env": {"default": "api"}}}],
    "components": {
        "securitySchemes": {"key": {"type": "apiKey", "in": "header", "name": "X-API-Key"}},
        "schemas": {"Pet": {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}},
    },
    "security": [{"key": []}],
    "paths": {
        "/pets/{petId}": {"get": {"summary": "Get pet", "tags": ["pets"],
                                  "parameters": [{"name": "petId", "in": "path", "required": True, "schema": {"type": "integer"}},
                                                 {"name": "verbose", "in": "query", "schema": {"type": "boolean"}}]}},
        "/pets": {"post": {"summary": "Add pet", "requestBody": {"content": {"application/json": {
            "schema": {"$ref": "#/components/schemas/Pet"}}}}}},
    },
}


def test_postman_import():
    result = cp.import_any(POSTMAN)
    reqs = result["collections"]["Shop"]
    assert [r["name"] for r in reqs] == ["Auth / Login", "Get item"]
    login, item = reqs
    assert login["body_type"] == "JSON" and login["auth"] == {"type": "No Auth"}
    assert login["tests"][0]["expected"] == "200"
    assert item["url"] == "{{baseUrl}}/items/42"
    assert item["params"] == [{"enabled": True, "key": "full", "value": "1"}]
    assert item["auth"] == {"type": "Bearer Token", "token": "{{TOKEN}}"}
    assert result["environments"]["Shop"] == {"baseUrl": "http://example.invalid"}


def test_openapi_import():
    result = cp.import_any(OPENAPI)
    reqs = {r["name"]: r for r in result["collections"]["Pets"]}
    get = reqs["pets / Get pet"]
    assert get["url"] == "{{baseUrl}}/pets/{{petId}}"
    assert get["auth"]["type"] == "API Key" and get["auth"]["key"] == "X-API-Key"
    add = reqs["Add pet"]
    assert json.loads(add["body"]) == {"name": "string", "age": 0}
    env = result["environments"]["Pets"]
    assert env["baseUrl"] == "https://api.pets.io/v1" and "petId" in env and "apiKey" in env


def test_openapi_yaml():
    text = "openapi: 3.0.0\ninfo: {title: Y}\nservers: [{url: 'http://h'}]\npaths:\n  /a:\n    get: {summary: A}\n"
    result = cp.import_any(cp.parse_import_text(text))
    assert result["collections"]["Y"][0]["url"] == "{{baseUrl}}/a"


def run_cli(argv):
    out = io.StringIO()
    args = cp.build_cli_parser().parse_args(argv)
    try:
        code = cp.cli_run(args, out)
    except cp.CliError as e:
        return 2, str(e)
    return code, out.getvalue()


def chain_collection(base):
    login = cp._empty_request("Login", "POST", "{{baseUrl}}/login")
    login.update(body_type="JSON", body='{"user": "ann", "password": "pw"}',
                 captures=[{"variable": "TOKEN", "source": "JSON path", "expression": "$.token"}],
                 tests=[{"source": "Status code", "operator": "equals", "expected": "200"}])
    me = cp._empty_request("Me", "GET", "{{baseUrl}}/me")
    me.update(auth={"type": "Bearer Token", "token": "{{TOKEN}}"},
              tests=[{"source": "JSON path", "property": "$.name", "operator": "equals", "expected": "{{who}}"}])
    return {"Chain": [login, me]}


def test_cli_run_pass_and_fail(server, tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(chain_collection(server)))
    junit, report = tmp_path / "r.xml", tmp_path / "r.json"
    code, out = run_cli(["run", "--file", str(f), "--var", f"baseUrl={server}", "--var", "who=ann",
                         "--junit", str(junit), "--json", str(report)])
    assert code == 0, out
    assert "PASSED" in out and junit.read_text().count("<testcase") == 2
    assert json.loads(report.read_text())["summary"]["tests_passed"] == 2

    code, out = run_cli(["run", "--file", str(f), "--var", f"baseUrl={server}", "--var", "who=bob"])
    assert code == 1 and "FAILED" in out and "actual: ann" in out


def test_cli_env_file_and_errors(server, tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps(chain_collection(server)))
    env = tmp_path / "env.json"
    env.write_text(json.dumps({"name": "ci", "values": [{"key": "baseUrl", "value": server, "enabled": True},
                                                        {"key": "who", "value": "ann", "enabled": True}]}))
    code, out = run_cli(["run", "--file", str(f), "--env-file", str(env)])
    assert code == 0, out
    code, msg = run_cli(["run", "--file", str(tmp_path / "missing.json")])
    assert code == 2
    code, msg = run_cli(["run", "Nope", "--file", str(f)])
    assert code == 2 and "not found" in msg


def test_cli_postman_file(server, tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps(POSTMAN))
    code, out = run_cli(["run", "--file", str(f), "--var", f"baseUrl={server}", "--bail"])
    # Login passes; Get item hits /items/42 which the test server answers with 404 but has no tests.
    assert code == 0, out
    assert out.count("PASS") >= 2


def test_postman_export_round_trip(tmp_path):
    original = cp.import_any(POSTMAN)["collections"]["Shop"]
    original[1]["tests"] = [
        {"enabled": True, "source": "Status code", "property": "", "operator": "equals", "expected": "200"},
        {"enabled": True, "source": "Response time (ms)", "property": "", "operator": "<", "expected": "500"},
        {"enabled": True, "source": "Header", "property": "X-Id", "operator": "exists", "expected": ""},
        {"enabled": True, "source": "Body", "property": "", "operator": "contains", "expected": "ok"},
        {"enabled": True, "source": "JSON path", "property": "$.a", "operator": "equals", "expected": "1"},
    ]
    gql = cp._empty_request("GQL", "POST", "{{baseUrl}}/graphql")
    gql.update(body_type="GraphQL", body="{ me { id } }", graphql_variables='{"a": 1}')
    binary = cp._empty_request("Upload", "PUT", "{{baseUrl}}/upload")
    binary.update(body_type="Binary", binary_file="/tmp/file.bin")
    form = cp._empty_request("Form", "POST", "{{baseUrl}}/form")
    form.update(body_type="Form Data", body="a=1&b=2")

    exported, warnings = cp.export_postman_collection("Shop", original + [gql, binary, form],
                                                      {"baseUrl": "http://h"})
    assert exported["info"]["schema"].endswith("v2.1.0/collection.json")
    assert exported["item"][0]["name"] == "Auth" and exported["item"][0]["item"][0]["name"] == "Login"
    assert any("no Postman equivalent" in w for w in warnings)

    back = cp.import_any(json.loads(json.dumps(exported)))
    reqs = {r["name"]: r for r in back["collections"]["Shop"]}
    assert reqs["Auth / Login"]["body_type"] == "JSON"
    assert reqs["Auth / Login"]["auth"] == {"type": "No Auth"}
    item = reqs["Get item"]
    assert item["url"] == "{{baseUrl}}/items/42"
    assert item["params"] == [{"enabled": True, "key": "full", "value": "1"}]
    assert item["auth"] == {"type": "Bearer Token", "token": "{{TOKEN}}"}
    assert [(t["source"], t["operator"]) for t in item["tests"]] == [
        ("Status code", "equals"), ("Response time (ms)", "<"), ("Header", "exists"), ("Body", "contains")]
    assert reqs["GQL"]["body_type"] == "GraphQL" and reqs["GQL"]["graphql_variables"] == '{"a": 1}'
    assert reqs["Upload"]["body_type"] == "Binary" and reqs["Upload"]["binary_file"] == "/tmp/file.bin"
    assert reqs["Form"]["body_type"] == "Form Data" and reqs["Form"]["body"] == "a=1&b=2"
    assert back["environments"]["Shop"] == {"baseUrl": "http://h"}
