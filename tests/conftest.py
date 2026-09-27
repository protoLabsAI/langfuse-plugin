"""Test bootstrap — import the plugin as a package with NO protoAgent host.

The host loads a plugin under a synthetic package; the suite does the same so the
modules' relative imports resolve standalone. Host-only imports live inside functions.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = "langfuse_plugin"

if PKG not in sys.modules:
    _spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[PKG] = _mod
    _spec.loader.exec_module(_mod)

CFG = {"public_key": "pk-lf-test", "secret_key": "sk-lf-test", "host": "https://lf.example"}


class FakeLangfuse:
    """A MockTransport-backed fake: route (METHOD, path) → (status, json). Records requests."""

    def __init__(self):
        self.routes: dict[tuple[str, str], tuple[int, object]] = {}
        self.requests: list[httpx.Request] = []

    def on(self, method: str, path: str, payload: object, status: int = 200) -> FakeLangfuse:
        self.routes[(method.upper(), path)] = (status, payload)
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, payload = self.routes.get((request.method, request.url.path), (404, {"message": "not found"}))
        if isinstance(payload, str):
            return httpx.Response(status, text=payload)
        return httpx.Response(status, json=payload)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def last_json(self) -> object:
        return json.loads(self.requests[-1].content)


@pytest.fixture
def fake() -> FakeLangfuse:
    return FakeLangfuse()


@pytest.fixture(autouse=True)
def _no_ambient_langfuse_env(monkeypatch):
    for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "LANGFUSE_BASE_URL"):
        monkeypatch.delenv(key, raising=False)


class Registry:
    def __init__(self, config=None, host=None):
        self.config = config or {}
        self.host = host
        self.tools, self.skill_dirs = [], []

    def register_tool(self, t):
        self.tools.append(t)

    def register_skill_dir(self, path):
        self.skill_dirs.append(path)


@pytest.fixture
def registry_cls():
    return Registry
