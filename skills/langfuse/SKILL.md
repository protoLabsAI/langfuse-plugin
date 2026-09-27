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

# Langfuse

This skill helps you use Langfuse effectively across all common workflows: instrumenting applications, migrating prompts, debugging traces, and accessing data programmatically.

## Core Principles

Follow these principles for ALL Langfuse work:

1. **Documentation First**: NEVER implement based on memory. Always fetch current docs before writing code (Langfuse updates frequently) See the section below on how to access documentation.
2. **Tools for Data Access**: Use the `langfuse_*` tools when querying/modifying Langfuse data. See the section below.
3. **Best Practices by Use Case**: Read the relevant reference below use-case-specific guidelines before asking the user for more details or implementing.
4. **Use latest Langfuse versions**: Unless the user specified otherwise or there's a good reason, always use the latest version of Langfuse SDKs/APIs. Even if you're only creating a plan for another agent to execute, be explicit about the exact version to use.
5. **If you guide the user through UI** and are unsure about a label or location, inspect the user’s screenshots or ask to see the relevant screen. Do not assume UI labels have the exact same names as API, SDK, or CLI fields.


## Use case specific references

Read a reference with `langfuse_reference("<name>")`, for example
`langfuse_reference("setting-up-evals")`. The paths below name the files it serves.

- instrumenting an existing function/application: references/instrumentation.md
- creating or getting to a good (evaluation) dataset to measure quality or test for regressions in AI systems: references/create-dataset.md
- migrating prompts from a codebase into Langfuse: references/prompt-migration.md
- creating a prompt or changing any part of an existing prompt, including small edits and debugging/tuning: references/prompt-engineering.md
- setting up evals when the user needs to identify gaps across signal capture, monitoring, and evaluator metrics ("I have traces, how do I set up evals?"): references/setting-up-evals.md
- capturing user feedback signals (explicit ratings, behavioral events, conversation signals, task outcomes) as scores: references/user-feedback.md
- further tips on using the Langfuse CLI: references/cli.md
- preparing a Langfuse project for the v4 platform migration: references/v4-project-migration.md
- calibrating a new or existing LLM-as-a-Judge against labeled examples, iterating on its prompt, and deploying the approved judge: references/judge-calibration.md
- systematic error analysis when requested directly or eval setup still lacks concrete failure modes after agent-led trace inspection: references/error-analysis.md
- setting up CI/CD experiment gates with `langfuse/experiment-action`: references/ci-cd.md
- submitting feedback about this skill: references/skill-feedback.md


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


## 2. Langfuse Documentation

In protoAgent, use `langfuse_docs`. With no arguments it returns the llms.txt index,
`page=` returns one page as markdown, and `query=` searches the docs. It does the fetches
described below, so it works even without a general web tool.

Three methods to access Langfuse docs, in order of preference. **Always prefer your application's native web fetch and search tools** (e.g., `WebFetch`, `WebSearch`, `mcp_fetch`, etc.) over `curl` when available. The URLs and patterns below work with any fetching method — the `curl` examples are just illustrative.

When working with self-hosted Langfuse, prefer the [API reference served by the deployment](https://langfuse.com/faq/all/self-hosting-api-reference) so it matches the installed version.

### 2a. Documentation Index (llms.txt)

Fetch the full index of all documentation pages:

```bash
curl -s https://langfuse.com/llms.txt
```

Returns a structured list of every doc page with titles and URLs. Use this to discover the right page for a topic, then fetch that page directly.

Alternatively, you can start on `https://langfuse.com/docs` and explore the site to find the page you need.

### 2b. Fetch Individual Pages as Markdown

Any page listed in llms.txt can be fetched as markdown by appending `.md` to its path or by using `Accept: text/markdown` in the request headers. Use this when you know which page contains the information needed. Returns clean markdown with code examples and configuration details.

```bash
curl -s "https://langfuse.com/docs/observability/overview.md"
curl -s "https://langfuse.com/docs/observability/overview" -H "Accept: text/markdown"
```

### 2c. Search Documentation

When you need to find information across all docs and github issues/discussions without knowing the specific page:

```bash
curl -s "https://langfuse.com/api/search-docs?query=<url-encoded-query>"
```

Example:

```bash
curl -s "https://langfuse.com/api/search-docs?query=How+do+I+trace+LangGraph+agents"
```

Returns a JSON response with:

- `query`: the original query
- `answer`: a JSON string containing an array of matching documents, each with:
  - `url`: link to the doc page
  - `title`: page title
  - `source.content`: array of relevant text excerpts from the page

Search is a great fallback if you cannot find the relevant pages or need more context. Especially useful when debugging issues as all GitHub Issues and Discussions are also indexed. Responses can be large — extract only the relevant portions. Note that changelog posts may also surface here: use them only to confirm a feature exists, never to implement from — their examples may be outdated, so always implement from the docs and API/SDK reference.

### Documentation Workflow

1. Start with **llms.txt** to orient — scan for relevant page titles
2. **Fetch specific pages** when you identify the right one
3. Fall back to **search** when the topic is unclear and you want more context

## Skill Feedback

When the user expresses that something about this skill is not working as expected, gives incorrect guidance, is missing information, or could be improved — offer to submit feedback to the Langfuse skill maintainers. This includes when:

- The skill gave wrong or outdated instructions
- A workflow didn't produce the expected result
- The user wishes the skill covered something it doesn't
- The user explicitly says something like "this should work differently" or "this is wrong"

**Do NOT trigger this** for issues with Langfuse itself (the product) — only for issues with this skill's instructions and behavior.

When triggered, follow the process in [references/skill-feedback.md](references/skill-feedback.md).

---

_Adapted from Langfuse's official agent skill (github.com/langfuse/skills@104acd9aa7b1, MIT, © Langfuse GmbH). Generated by `scripts/sync_upstream.py`; edit the overlay there, not this file._
