"""langfuse — Langfuse's official agent skill plus native data tools for protoAgent.

register() is the only place plugin code runs. Host-only imports stay lazy (inside
functions) so the test suite imports these modules with no protoAgent host present.
"""

from __future__ import annotations

import logging

log = logging.getLogger("protoagent.plugins.langfuse")


def register(registry) -> None:
    cfg = dict(registry.config or {})
    host = getattr(registry, "host", None)
    host_config = getattr(host, "config", None) if host is not None else None

    try:
        from .tools import build_tools

        for t in build_tools(cfg, host_config):
            registry.register_tool(t)
    except Exception:  # one failing group must not sink the rest
        log.exception("[langfuse] registering tools failed")

    try:
        registry.register_skill_dir("skills")
    except Exception:
        log.exception("[langfuse] registering skills failed")

    log.info("[langfuse] registered (writes %s)", "enabled" if cfg.get("allow_writes") else "disabled")
