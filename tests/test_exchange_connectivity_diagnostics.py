import socket
from urllib import error

from scripts import diagnose_exchange_connectivity as diag


class FakeResponse:
    def __init__(self, body=b'{"serverTime": 1}'):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


def _checks_by_exchange_and_scope(report):
    return {(check["exchange"], check["scope"]): check for check in report["checks"]}


def test_public_diagnostics_pass_with_fake_endpoint(monkeypatch):
    monkeypatch.setattr(diag.request, "urlopen", lambda *_, **__: FakeResponse())

    report = diag.run_diagnostics(exchanges=["okx"], timeout=0.1, use_proxy=False)
    check = _checks_by_exchange_and_scope(report)[("okx", "public")]

    assert report["success"] is True
    assert check["status"] == "PASS"
    assert check["category"] == "ok"
    assert "latency_ms" in check


def test_public_diagnostics_classify_dns_failure(monkeypatch):
    def fail_dns(*_, **__):
        raise error.URLError(socket.gaierror("nodename nor servname provided"))

    monkeypatch.setattr(diag.request, "urlopen", fail_dns)

    report = diag.run_diagnostics(exchanges=["binance"], timeout=0.1, use_proxy=False)
    check = _checks_by_exchange_and_scope(report)[("binance", "public")]

    assert report["success"] is False
    assert check["status"] == "FAIL"
    assert check["category"] == "dns"


def test_private_diagnostics_fail_fast_when_credentials_are_missing(monkeypatch):
    for name in diag.PRIVATE_CREDENTIALS["okx"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(diag.request, "urlopen", lambda *_, **__: FakeResponse())

    report = diag.run_diagnostics(
        exchanges=["okx"],
        include_private=True,
        timeout=0.1,
        use_proxy=False,
    )
    checks = _checks_by_exchange_and_scope(report)

    assert checks[("okx", "public")]["status"] == "PASS"
    assert checks[("okx", "private")]["status"] == "FAIL"
    assert checks[("okx", "private")]["category"] == "credential"
    assert set(checks[("okx", "private")]["details"]["missing"]) == set(diag.PRIVATE_CREDENTIALS["okx"])


def test_error_classifier_distinguishes_proxy_rate_limit_and_credentials():
    assert diag._classify_error(error.URLError("Tunnel connection failed: proxy refused")) == "proxy"
    assert diag._classify_error(error.HTTPError("url", 429, "Too Many Requests", {}, None)) == "rate_limit"
    assert diag._classify_error(error.HTTPError("url", 401, "Unauthorized", {}, None)) == "credential"
