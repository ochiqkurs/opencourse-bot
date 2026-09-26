import asyncio

import httpx

import api


class _FailingClient:
    """Stub httpx.AsyncClient whose post always raises a network error."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        raise httpx.ConnectError("boom")


class _OKClient:
    """Stub httpx.AsyncClient that returns a 200 with a short_code."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return httpx.Response(200, json={"short_code": "123456"})


def test_confirm_auth_network_error_returns_zero(monkeypatch):
    monkeypatch.setattr(api, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(api.httpx, "AsyncClient", _FailingClient)
    status = asyncio.run(api.confirm_auth("tok", 1, "f", "l", "u"))
    assert status == 0


def test_issue_code_success(monkeypatch):
    monkeypatch.setattr(api.httpx, "AsyncClient", _OKClient)
    status, code = asyncio.run(api.issue_code(1, "f", "l", "u"))
    assert status == 200
    assert code == "123456"


def test_issue_code_network_error_returns_zero_none(monkeypatch):
    monkeypatch.setattr(api, "RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr(api.httpx, "AsyncClient", _FailingClient)
    status, code = asyncio.run(api.issue_code(1, "f", "l", "u"))
    assert status == 0
    assert code is None


def test_report_start_swallows_network_errors(monkeypatch):
    # Telemetry is best-effort: a network failure must never raise.
    monkeypatch.setattr(api.httpx, "AsyncClient", _FailingClient)
    result = asyncio.run(
        api.report_start(1, 1, "f", "l", "u", "uz", has_token=True)
    )
    assert result is None
