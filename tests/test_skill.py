from __future__ import annotations

import importlib.util
import re

import yaml

from conftest import CFG, ROOT
from langfuse_plugin.tools import build_tools

SKILL = ROOT / "skills" / "langfuse" / "SKILL.md"
_spec = importlib.util.spec_from_file_location("sync_upstream", ROOT / "scripts" / "sync_upstream.py")
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


def _frontmatter() -> dict:
    return yaml.safe_load(SKILL.read_text(encoding="utf-8").split("---", 2)[1])


def test_the_committed_skill_is_what_the_overlay_composes():
    """Edit scripts/sync_upstream.py, then `python scripts/sync_upstream.py --compose`."""
    assert SKILL.read_text(encoding="utf-8") == sync.compose((ROOT / "vendor/langfuse/SKILL.md").read_text("utf-8"))


def test_frontmatter_meets_the_hosts_loader_rules():
    meta = _frontmatter()
    assert meta["name"] == "langfuse"
    assert 0 < len(meta["description"]) <= 1024  # the loader truncates past 1024


def test_the_skill_lists_exactly_the_read_tools():
    read_tools = {t.name for t in build_tools(CFG)}
    assert set(_frontmatter()["tools"]) == read_tools


def test_every_reference_the_skill_names_exists():
    named = set(re.findall(r"references/([\w-]+)\.md", SKILL.read_text(encoding="utf-8")))
    on_disk = {p.stem for p in (ROOT / "skills/langfuse/references").glob("*.md")}
    assert named and named <= on_disk


def test_no_shell_cli_instructions_leak_into_the_data_access_section():
    body = SKILL.read_text(encoding="utf-8")
    section = body[body.index("## 1. Langfuse data via tools") : body.index("## 2. Langfuse Documentation")]
    assert "export LANGFUSE_SECRET_KEY" not in section


def test_register_contributes_the_tools_and_the_skill_dir(registry_cls):
    import langfuse_plugin

    reg = registry_cls(config=CFG)
    langfuse_plugin.register(reg)
    assert len(reg.tools) == len(build_tools(CFG)) and reg.skill_dirs == ["skills"]


def test_compose_fails_loudly_if_upstream_rewords_the_cli_principle():
    import pytest

    upstream = (ROOT / "vendor/langfuse/SKILL.md").read_text("utf-8").replace("CLI for Data Access", "Data via CLI")
    with pytest.raises(ValueError, match="principle 2"):
        sync.compose(upstream)
