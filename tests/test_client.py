from __future__ import annotations

import base64
import sys
import types

import pytest

from conftest import CFG
from langfuse_plugin.client import LangfuseClient, LangfuseError, resolve_credentials


def test_plugin_config_wins():
    creds = resolve_credentials(CFG)
    assert (creds.public_key, creds.host, creds.source) == ("pk-lf-test", "https://lf.example", "plugin")


def test_falls_back_to_the_hosts_tracing_credentials(monkeypatch):
    # The host resolver is imported lazily; stand one in for it.
    fake_mod = types.ModuleType("observability.tracing")
    fake_mod.resolve_credentials = lambda config: ("pk-host", "sk-host", "https://host.lf/", "config")
    monkeypatch.setitem(sys.modules, "observability", types.ModuleType("observability"))
    monkeypatch.setitem(sys.modules, "observability.tracing", fake_mod)

    creds = resolve_credentials({}, host_config=lambda: object())

    assert (creds.public_key, creds.host, creds.source) == ("pk-host", "https://host.lf", "host-tracing")


def test_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-env")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-env")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://env.lf")
    assert resolve_credentials({}).source == "env"


def test_unconfigured_is_a_readable_error():
    with pytest.raises(LangfuseError, match="not configured"):
        resolve_credentials({})


def test_requests_carry_basic_auth_an_explicit_user_agent_and_clean_params(fake):
    fake.on("GET", "/api/public/traces", {"data": []})
    LangfuseClient(CFG, transport=fake.transport).get("/api/public/traces", limit=5, name="", tags=None)

    req = fake.requests[-1]
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"pk-lf-test:sk-lf-test").decode()
    assert req.headers["user-agent"].startswith("protoagent-langfuse-plugin")
    assert dict(req.url.params) == {"limit": "5"}


def test_only_public_api_paths_are_allowed(fake):
    with pytest.raises(LangfuseError, match="/api/public/"):
        LangfuseClient(CFG, transport=fake.transport).get("/api/admin/users")


def test_401_names_the_credential_source(fake):
    fake.on("GET", "/api/public/traces", {"message": "no"}, status=401)
    with pytest.raises(LangfuseError, match=r"401.*plugin"):
        LangfuseClient(CFG, transport=fake.transport).get("/api/public/traces")


def test_an_unreachable_host_error_carries_no_userinfo(fake):
    import httpx

    def boom(request):
        raise httpx.ConnectError("refused")

    cfg = {**CFG, "host": "https://user:hunter2@lf.example"}
    with pytest.raises(LangfuseError) as err:
        LangfuseClient(cfg, transport=httpx.MockTransport(boom)).get("/api/public/traces")
    assert "hunter2" not in str(err.value) and "lf.example" in str(err.value)
