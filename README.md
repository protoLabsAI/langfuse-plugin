# langfuse-plugin

A [protoAgent](https://github.com/protoLabsAI/protoAgent) plugin that gives an agent
[Langfuse](https://langfuse.com): Langfuse's official agent skill, plus native tools for
reading and (optionally) writing Langfuse data.

- **The skill.** Langfuse's own [agent skill](https://langfuse.com/docs/api-and-data-platform/features/agent-skill)
  ([github.com/langfuse/skills](https://github.com/langfuse/skills), MIT) covers
  instrumentation, prompt migration and engineering, datasets, evals, user feedback, LLM-judge
  calibration, error analysis and CI/CD gates. It is vendored verbatim, with a small overlay
  that points its data access at the tools below instead of `npx langfuse-cli`. The agent
  then needs no Node, and no API keys in its shell.
- **The tools.** Native access to the Langfuse public API, with compact output and capped
  input/output so a trace dump can't flood the context.

## Tools

| Tool | What it does |
|---|---|
| `langfuse_traces` | List traces. Filter by name, session, user, tags, environment or `since_hours`, and order by e.g. `latency.desc` / `totalCost.desc`. Each trace includes a UI link. |
| `langfuse_trace` | One trace, with its scores and an indented observation tree (latency, model, tokens, cost, level). Reports the root count and flags **ORPHAN** spans whose parent is missing from the trace. |
| `langfuse_observations` | Spans, generations and tool calls across traces. |
| `langfuse_sessions` | List sessions, or one session's traces in order. |
| `langfuse_scores` | Scores: evaluations, feedback, annotations. |
| `langfuse_prompts` | List prompts, or get one prompt (by label or version). |
| `langfuse_datasets` | List datasets, or one dataset's items. |
| `langfuse_metrics` | Daily trace and observation counts, cost, and token usage per model. |
| `langfuse_api` | Any `/api/public/...` endpoint. GET always; other methods only with `allow_writes`. |
| `langfuse_docs` | Current Langfuse docs: the llms.txt index, one page as markdown, or a search. |
| `langfuse_reference` | The skill's use-case references (`setting-up-evals`, `instrumentation`, …). |
| `langfuse_score` ✎ | Attach a score to a trace or observation. |
| `langfuse_dataset_item` ✎ | Add a dataset item, e.g. a failing trace as a regression case, optionally creating the dataset. |

✎ Registered only when `allow_writes: true`.

## Install

```bash
python -m server plugin install https://github.com/protoLabsAI/langfuse-plugin
```

Installing doesn't enable it. Enable it in **Settings → Plugins** (or set `plugins.enabled`),
which is live and needs no restart.

## Configure

```yaml
langfuse:
  public_key: ""       # leave these three empty to use this agent's tracing credentials
  secret_key: ""       # a secret, stored in secrets.yaml
  host: ""
  allow_writes: false  # registers the write tools and allows non-GET langfuse_api calls
  max_io_chars: 1500   # cap on each input/output value a tool returns
  timeout_s: 20
```

Credentials resolve at call time, in order:
1. The plugin's own keys.
2. **This agent's tracing credentials** (`tracing.*` plus `secrets.yaml`; protoAgent 0.148.0 or later). An agent that already traces to Langfuse reads the same project with no extra setup.
3. The `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` environment.

Reading traces is safe, so the read tools are always on. Writing to the project (scores,
dataset items, prompt or config changes through `langfuse_api`) is an operator decision,
so it stays off until `allow_writes` is set.

## Langfuse versions

The read tools use the public read API of Langfuse v3 write-mode deployments (self-hosted
3.x, and Langfuse Cloud until its v3 read endpoints are removed on 2026-11-16).
`langfuse_scores` already prefers the v4-safe `/v3/scores`. Support for the v4 read APIs
(`/v2/observations`, `/v2/metrics`) is tracked in
[#2](https://github.com/protoLabsAI/langfuse-plugin/issues/2).

## Updating the vendored skill

```bash
python scripts/sync_upstream.py              # vendor langfuse/skills@main and re-compose
python scripts/sync_upstream.py --ref <sha>  # pin a commit
python scripts/sync_upstream.py --compose    # re-compose from vendor/ only
```

`vendor/langfuse/` holds upstream's `SKILL.md`, `LICENSE` and `UPSTREAM_SHA` verbatim.
`skills/langfuse/references/` is upstream's references, verbatim. `skills/langfuse/SKILL.md`
is **generated**, so edit the overlay in `scripts/sync_upstream.py`. `tests/test_skill.py`
fails when the committed skill drifts from what the overlay composes.

## Development

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt ruff
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/pytest -q
```

The suite is host-free: it needs no protoAgent install.

## License

MIT. The vendored Langfuse skill is MIT, © Langfuse GmbH (`vendor/langfuse/LICENSE`).
