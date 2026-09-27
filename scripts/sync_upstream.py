#!/usr/bin/env python3
"""Sync Langfuse's official agent skill into this plugin, and compose the protoAgent skill.

    python scripts/sync_upstream.py              # fetch github.com/langfuse/skills@main, then compose
    python scripts/sync_upstream.py --ref <sha>  # pin a specific upstream commit
    python scripts/sync_upstream.py --compose    # re-compose from vendor/ only (no network)

Upstream is vendored VERBATIM: ``vendor/langfuse/SKILL.md`` + ``LICENSE`` + ``UPSTREAM_SHA``,
and ``skills/langfuse/references/*.md`` (which the ``langfuse_reference`` tool serves).
``skills/langfuse/SKILL.md`` is GENERATED: upstream's body with the parts that assume a
coding agent with a shell (the ``npx langfuse-cli`` data-access section, curl doc fetches)
pointed at this plugin's native tools instead. ``tests/test_skill.py`` fails when the
committed SKILL.md drifts from what this script composes, so edit the overlay here, never
the generated file.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "langfuse"
SKILL_DIR = ROOT / "skills" / "langfuse"
UPSTREAM_REPO = "https://github.com/langfuse/skills.git"

FRONTMATTER = """\
---
name: langfuse
description: >-
  Work with Langfuse from protoAgent: query and debug traces, observations, sessions and
  scores; manage prompts and datasets; set up evals and user feedback; instrument code;
  look up current Langfuse docs. Use for any AI-observability, prompt-management,
  evaluation or dataset task, or when a Langfuse trace needs inspecting — even when
  Langfuse is not explicitly mentioned.
tools:
  - langfuse_traces
  - langfuse_trace
  - langfuse_observations
  - langfuse_sessions
  - langfuse_scores
  - langfuse_prompts
  - langfuse_datasets
  - langfuse_metrics
  - langfuse_api
  - langfuse_docs
  - langfuse_reference
---
"""

DATA_ACCESS = """\
## 1. Langfuse data via tools

In protoAgent, use the `langfuse_*` tools instead of `langfuse-cli`. They reach the same
public API with credentials the operator already configured: the plugin's own keys, or
this agent's tracing keys. Never ask the user to paste keys into chat.

- `langfuse_traces` lists traces (filters: name, session, user, tags, environment,
  `since_hours`, `order_by` such as `latency.desc` or `totalCost.desc`).
- `langfuse_trace` returns one trace with its scores and an indented observation tree.
  It reports the number of roots and flags ORPHAN spans whose parent is missing. That is
  usually a parent span that was never exported, or a run still in flight, since a span
  is exported when it ends.
- `langfuse_observations` queries spans, generations and tools across traces.
- `langfuse_sessions`, `langfuse_scores`, `langfuse_prompts`, `langfuse_datasets` and
  `langfuse_metrics` cover the rest of the common reads.
- `langfuse_api(method, path, params, body)` calls any `/api/public/...` endpoint the
  others don't cover. Look up the endpoint in the API reference with `langfuse_docs` first.
- Writes (`langfuse_score`, `langfuse_dataset_item`, and non-GET `langfuse_api` calls)
  exist only when the operator has set `langfuse.allow_writes: true`. If they're missing,
  say so rather than working around it.

When a reference below shows a `npx langfuse-cli api <resource> <action>` command, use
the matching tool, or `langfuse_api` with the same resource path. The CLI is only for a
human at a shell.
"""

REFERENCES_NOTE = """\
Read a reference with `langfuse_reference("<name>")`, for example
`langfuse_reference("setting-up-evals")`. The paths below name the files it serves.
"""

DOCS_NOTE = """\
In protoAgent, use `langfuse_docs`. With no arguments it returns the llms.txt index,
`page=` returns one page as markdown, and `query=` searches the docs. It does the fetches
described below, so it works even without a general web tool.
"""


def _section_bounds(body: str, heading: str) -> tuple[int, int]:
    """[start, end) of the ``## `` section whose heading line starts with ``heading``."""
    m = re.search(rf"^## {re.escape(heading)}.*$", body, re.M)
    if not m:
        raise ValueError(f"upstream SKILL.md has no section starting '## {heading}' — update the overlay")
    nxt = re.search(r"^## ", body[m.end() :], re.M)
    return m.start(), (m.end() + nxt.start()) if nxt else len(body)


def compose(upstream: str) -> str:
    """The protoAgent SKILL.md for a given upstream SKILL.md text."""
    parts = upstream.split("---", 2)
    if len(parts) < 3 or parts[0].strip():
        raise ValueError("upstream SKILL.md has no leading frontmatter block")
    body = parts[2].lstrip("\n")

    # Core principle 2 names the CLI; point it at the tools.
    body = body.replace(
        "2. **CLI for Data Access**: Use `langfuse-cli` when querying/modifying Langfuse data. "
        "See the section below on how to use the CLI.",
        "2. **Tools for Data Access**: Use the `langfuse_*` tools when querying/modifying Langfuse "
        "data. See the section below.",
    )

    start, end = _section_bounds(body, "1. Langfuse API via CLI")
    body = body[:start] + DATA_ACCESS + "\n\n" + body[end:]

    start, end = _section_bounds(body, "Use case specific references")
    heading_end = body.index("\n", start) + 1
    body = body[:heading_end] + "\n" + REFERENCES_NOTE + body[heading_end:]

    start, end = _section_bounds(body, "2. Langfuse Documentation")
    heading_end = body.index("\n", start) + 1
    body = body[:heading_end] + "\n" + DOCS_NOTE + body[heading_end:]

    sha = (VENDOR / "UPSTREAM_SHA").read_text(encoding="utf-8").strip() if (VENDOR / "UPSTREAM_SHA").exists() else ""
    footer = (
        "\n---\n\n_Adapted from Langfuse's official agent skill "
        f"(github.com/langfuse/skills{'@' + sha[:12] if sha else ''}, MIT, © Langfuse GmbH). "
        "Generated by `scripts/sync_upstream.py`; edit the overlay there, not this file._\n"
    )
    return FRONTMATTER + "\n" + body.rstrip() + "\n" + footer


def write_composed() -> Path:
    out = SKILL_DIR / "SKILL.md"
    out.write_text(compose((VENDOR / "SKILL.md").read_text(encoding="utf-8")), encoding="utf-8")
    return out


def fetch(ref: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["git", "clone", "-q", UPSTREAM_REPO, tmp], check=True)
        subprocess.run(["git", "-C", tmp, "checkout", "-q", ref], check=True)
        sha = subprocess.run(["git", "-C", tmp, "rev-parse", "HEAD"], check=True, capture_output=True, text=True)
        src = Path(tmp) / "skills" / "langfuse"
        VENDOR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / "SKILL.md", VENDOR / "SKILL.md")
        shutil.copy2(Path(tmp) / "LICENSE", VENDOR / "LICENSE")
        (VENDOR / "UPSTREAM_SHA").write_text(sha.stdout.strip() + "\n", encoding="utf-8")
        refs = SKILL_DIR / "references"
        if refs.exists():
            shutil.rmtree(refs)
        shutil.copytree(src / "references", refs)
    print(f"vendored langfuse/skills@{sha.stdout.strip()[:12]}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default="main", help="upstream git ref to vendor (default: main)")
    ap.add_argument("--compose", action="store_true", help="only re-compose SKILL.md from vendor/")
    args = ap.parse_args(argv)
    if not args.compose:
        fetch(args.ref)
    print(f"wrote {write_composed().relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
