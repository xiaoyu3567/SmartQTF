import json
import os
from pathlib import Path
from urllib import error, parse, request

import pytest


SMARTQTF_WEB_API_SMOKE_ENDPOINTS = (
    {"name": "status", "method": "GET", "path": "/api/smartqtf/status"},
    {"name": "start", "method": "POST", "path": "/api/smartqtf/start", "body": {"index": 2}},
    {
        "name": "run_once",
        "method": "POST",
        "path": "/api/smartqtf/run-once",
        "body": {"requested_at": 1700000600, "index": 4, "batch_id": "web-smoke"},
    },
    {"name": "kline", "method": "GET", "path": "/api/smartqtf/kline?symbol=BTCUSDT&timeframe=5m"},
    {"name": "testflow", "method": "GET", "path": "/api/smartqtf/testflow"},
    {"name": "logs", "method": "GET", "path": "/api/smartqtf/logs?limit=20"},
    {"name": "optimization", "method": "GET", "path": "/api/smartqtf/optimization"},
    {
        "name": "optimization_review",
        "method": "POST",
        "path": "/api/smartqtf/optimization/review",
        "body": {
            "action": "reject",
            "artifact_id": "artifact-001",
            "reviewer_note": "web smoke dry-run",
            "dry_run": True,
            "manual_review": True,
        },
        "live_optional": True,
    },
    {"name": "stop", "method": "POST", "path": "/api/smartqtf/stop", "body": {}},
)


SECRET_KEY_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "passphrase",
    "password",
    "private_key",
    "secret",
    "token",
)


WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


def test_smartqtf_web_api_smoke_matrix_covers_required_worker_surfaces():
    names = [endpoint["name"] for endpoint in SMARTQTF_WEB_API_SMOKE_ENDPOINTS]

    assert names == [
        "status",
        "start",
        "run_once",
        "kline",
        "testflow",
        "logs",
        "optimization",
        "optimization_review",
        "stop",
    ]
    assert all(endpoint["path"].startswith("/api/smartqtf/") for endpoint in SMARTQTF_WEB_API_SMOKE_ENDPOINTS)
    assert {endpoint["method"] for endpoint in SMARTQTF_WEB_API_SMOKE_ENDPOINTS} == {"GET", "POST"}
    assert "symbol=BTCUSDT" in _endpoint("kline")["path"]
    assert "timeframe=5m" in _endpoint("kline")["path"]
    assert _endpoint("start")["body"] == {"index": 2}
    assert _endpoint("run_once")["body"]["batch_id"] == "web-smoke"
    assert _endpoint("optimization_review")["body"]["dry_run"] is True
    assert _endpoint("optimization_review")["body"]["manual_review"] is True


def test_smartqtf_internal_web_app_files_cover_runtime_console_contract():
    assert (WEB_ROOT / "package.json").is_file()
    assert (WEB_ROOT / "app/smartqtf/page.tsx").is_file()
    assert (WEB_ROOT / "components/RuntimeConsole.tsx").is_file()
    assert (WEB_ROOT / "lib/smartqtf-api.ts").is_file()
    assert (WEB_ROOT / "lib/smartqtf-client.ts").is_file()

    for endpoint in SMARTQTF_WEB_API_SMOKE_ENDPOINTS:
        route_path = endpoint["path"].split("?", 1)[0].removeprefix("/api/smartqtf/")
        assert (WEB_ROOT / f"app/api/smartqtf/{route_path}/route.ts").is_file(), endpoint["name"]

    runtime_console = (WEB_ROOT / "components/RuntimeConsole.tsx").read_text()
    for visible_label in ("Main", "TestFlow", "Logs", "Optimization"):
        assert visible_label in runtime_console
    for control_label in ("Start Scan Loop", "Stop", "Run Once", "Refresh"):
        assert control_label in runtime_console
    for optimization_label in (
        "Validation",
        "Gate",
        "Artifacts",
        "OOS Evidence",
        "Walk-Forward Evidence",
        "Monte Carlo Evidence",
        "missing_out_of_sample_validation",
        "missing_walk_forward_validation",
        "missing_monte_carlo_validation",
        "Live orders",
        "Analytics live state",
        "Key material",
    ):
        assert optimization_label in runtime_console
    for review_label in ("Promotion Review", "Approve", "Reject", "Review note"):
        assert review_label in runtime_console
    for timeframe_label in ("5m execution", "15m context", "1h context", "4h context"):
        assert timeframe_label in runtime_console


def test_smartqtf_internal_web_proxy_does_not_expose_secret_key_names():
    source_paths = [
        WEB_ROOT / "lib/smartqtf-api.ts",
        WEB_ROOT / "lib/smartqtf-client.ts",
        WEB_ROOT / "components/RuntimeConsole.tsx",
    ]
    combined = "\n".join(path.read_text().lower() for path in source_paths)

    assert "smartqtf_worker_url" in combined
    assert "browser direct" not in combined
    assert "api_key" in combined
    assert "passphrase" in combined
    assert "private_key" in combined


def test_smartqtf_internal_web_proxy_injects_safe_default_worker_config():
    proxy_source = (WEB_ROOT / "lib/smartqtf-api.ts").read_text()

    assert "paper-runtime.example.json" in proxy_source
    assert "SMARTQTF_WORKER_CONFIG" in proxy_source
    assert "withDefaultWorkerConfig" in proxy_source
    assert 'proxyPath !== "/start" && proxyPath !== "/run-once"' in proxy_source


def test_smartqtf_web_api_smoke_against_running_next_app_when_configured():
    base_url = os.environ.get("SMARTQTF_WEB_SMOKE_URL")
    if not base_url:
        pytest.skip("Set SMARTQTF_WEB_SMOKE_URL after starting the SmartQTF web app to run the live web API smoke matrix")

    responses = []
    for endpoint in SMARTQTF_WEB_API_SMOKE_ENDPOINTS:
        if endpoint.get("live_optional"):
            continue
        status_code, payload = _request_json(base_url, endpoint)
        assert isinstance(payload, dict), endpoint["name"]
        assert status_code < 500, endpoint["name"]
        assert payload.get("ok") is True, endpoint["name"]
        assert not _contains_secret_key(payload), endpoint["name"]
        responses.append((endpoint["name"], status_code, payload))

    assert [name for name, _status_code, _payload in responses] == [
        endpoint["name"]
        for endpoint in SMARTQTF_WEB_API_SMOKE_ENDPOINTS
        if not endpoint.get("live_optional")
    ]


def _endpoint(name):
    return next(endpoint for endpoint in SMARTQTF_WEB_API_SMOKE_ENDPOINTS if endpoint["name"] == name)


def _request_json(base_url, endpoint):
    url = parse.urljoin(base_url.rstrip("/") + "/", endpoint["path"].lstrip("/"))
    body = endpoint.get("body")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=endpoint["method"])

    try:
        with request.urlopen(req, timeout=10) as response:
            status_code = response.status
            raw_body = response.read()
    except error.HTTPError as exc:
        status_code = exc.code
        raw_body = exc.read()
    except error.URLError as exc:
        pytest.fail(f"{endpoint['name']} could not reach SmartQTF web API: {exc}")

    try:
        return status_code, json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        pytest.fail(f"{endpoint['name']} did not return JSON: {exc}")


def _contains_secret_key(value):
    if isinstance(value, dict):
        for key, item in value.items():
            lowered_key = str(key).lower()
            if any(fragment in lowered_key for fragment in SECRET_KEY_FRAGMENTS):
                return True
            if _contains_secret_key(item):
                return True
    if isinstance(value, list):
        return any(_contains_secret_key(item) for item in value)
    return False
