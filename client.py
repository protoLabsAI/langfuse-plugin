"""A small, synchronous Langfuse public-API client for the plugin's tools.

Credentials resolve at CALL time, in order:

1. the plugin's own config (``langfuse.public_key`` / ``secret_key`` / ``host``);
2. the host agent's tracing credentials (``observability.tracing.resolve_credentials``,
   protoAgent >= 0.148.0), so an agent that already traces to Langfuse needs no extra
   setup — the plugin reads the project it writes to;
3. the ``LANGFUSE_PUBLIC_KEY`` / ``LANGFUSE_SECRET_KEY`` / ``LANGFUSE_HOST`` (or
   ``LANGFUSE_BASE_URL``) environment, for a host-free run.

The host import is lazy and guarded: this module imports with only ``httpx`` present.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

USER_AGENT = "protoagent-langfuse-plugin"
DEFAULT_TIMEOUT_S = 20.0


class LangfuseError(Exception):
    """A request that could not be completed. The message is safe to show the model."""


@dataclass(frozen=True)
class Credentials:
    public_key: str
    secret_key: str
    host: str
    source: str  # "plugin" | "host-tracing" | "env"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _host_credentials(host_config: Callable[[], Any] | None) -> Credentials | None:
    """The agent's own tracing keys, via the host's resolver. None off-host or unset."""
    try:
        from observability.tracing import resolve_credentials  # host-only, lazy
    except Exception:  # off-host, or a host older than 0.148.0
        return None
    try:
        config = host_config() if host_config is not None else None
        public_key, secret_key, host, _source = resolve_credentials(config)
    except Exception:  # a resolver failure falls through to env
        return None
    if _clean(public_key) and _clean(secret_key) and _clean(host):
        return Credentials(_clean(public_key), _clean(secret_key), _clean(host).rstrip("/"), "host-tracing")
    return None


def resolve_credentials(cfg: dict, host_config: Callable[[], Any] | None = None) -> Credentials:
    """Plugin config, then the host's tracing keys, then the environment."""
    public_key, secret_key = _clean(cfg.get("public_key")), _clean(cfg.get("secret_key"))
    if public_key and secret_key:
        host = _clean(cfg.get("host")) or _clean(os.environ.get("LANGFUSE_HOST")) or "https://cloud.langfuse.com"
        return Credentials(public_key, secret_key, host.rstrip("/"), "plugin")
    creds = _host_credentials(host_config)
    if creds is not None:
        return creds
    public_key = _clean(os.environ.get("LANGFUSE_PUBLIC_KEY"))
    secret_key = _clean(os.environ.get("LANGFUSE_SECRET_KEY"))
    host = _clean(os.environ.get("LANGFUSE_HOST")) or _clean(os.environ.get("LANGFUSE_BASE_URL"))
    if public_key and secret_key and host:
        return Credentials(public_key, secret_key, host.rstrip("/"), "env")
    raise LangfuseError(
        "Langfuse is not configured. Set the plugin's public_key/secret_key/host in Settings, "
        "or enable tracing on this agent (tracing.enabled + keys), or export "
        "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST."
    )


class LangfuseClient:
    """Thin wrapper over the public REST API (``/api/public/...``)."""

    def __init__(
        self,
        cfg: dict,
        host_config: Callable[[], Any] | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._cfg = cfg
        self._host_config = host_config
        self._transport = transport  # tests inject a MockTransport

    def credentials(self) -> Credentials:
        return resolve_credentials(self._cfg, self._host_config)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        body: Any = None,
    ) -> Any:
        if not path.startswith("/api/public/"):
            raise LangfuseError(f"path must start with /api/public/ (got {path!r})")
        creds = self.credentials()
        clean_params = {k: v for k, v in (params or {}).items() if v not in (None, "", [])}
        try:
            timeout = float(self._cfg.get("timeout_s") or DEFAULT_TIMEOUT_S)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_S
        try:
            with httpx.Client(
                base_url=creds.host,
                auth=(creds.public_key, creds.secret_key),
                # An explicit UA: some self-hosted deployments sit behind a WAF that
                # 403s generic library user agents.
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
                transport=self._transport,
            ) as http:
                resp = http.request(method.upper(), path, params=clean_params, json=body)
        except httpx.HTTPError as exc:
            raise LangfuseError(f"could not reach Langfuse at {creds.host}: {type(exc).__name__}: {exc}") from exc
        if resp.status_code == 401:
            raise LangfuseError(f"Langfuse rejected the credentials (401) from source {creds.source!r}")
        if resp.status_code >= 400:
            raise LangfuseError(f"Langfuse returned {resp.status_code} for {method.upper()} {path}: {resp.text[:300]}")
        if not resp.content:
            return {}
        try:
            return resp.json()
        except json.JSONDecodeError:
            return {"text": resp.text[:2000]}

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: Any) -> Any:
        return self.request("POST", path, body=body)

    def trace_url(self, trace_id: str, project_id: str = "") -> str:
        """A link into the Langfuse UI for a trace (best-effort: needs the project id)."""
        host = self.credentials().host
        return f"{host}/project/{project_id}/traces/{trace_id}" if project_id else ""
