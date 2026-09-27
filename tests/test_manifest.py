from __future__ import annotations

import re

import yaml

from conftest import ROOT

MANIFEST = yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text(encoding="utf-8"))


def test_ships_disabled_with_its_secret_routed():
    assert MANIFEST["enabled"] is False
    assert MANIFEST["id"] == MANIFEST["config_section"] == "langfuse"
    assert MANIFEST["secrets"] == ["secret_key"]
    assert "secret_key" in MANIFEST["config"]


def test_writes_are_off_by_default():
    assert MANIFEST["config"]["allow_writes"] is False


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", MANIFEST["version"])
