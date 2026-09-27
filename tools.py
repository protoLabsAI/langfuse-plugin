"""The agent-facing tools. Outputs are compact JSON, with IO capped, so a trace dump
can't flood the context window."""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from langchain_core.tools import tool

from .client import USER_AGENT, LangfuseClient, LangfuseError

REFERENCES_DIR = Path(__file__).resolve().parent / "skills" / "langfuse" / "references"
DOCS_HOST = "https://langfuse.com"
_MAX_DOC_CHARS = 20_000


def _cap(value: Any, limit: int) -> Any:
    """``value`` as-is when short, else its JSON text truncated to ``limit`` chars."""
    if value in (None, "", {}, []):
        return value
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= limit:
        return value
    return f"{text[:limit]}… [{len(text) - limit} more chars]"


def _since(hours: float | int | None) -> str | None:
    if not hours:
        return None
    start = dt.datetime.now(dt.UTC) - dt.timedelta(hours=float(hours))
    return start.strftime("%Y-%m-%dT%H:%M:%SZ")


def _limit(value: int | None, default: int, ceiling: int = 100) -> int:
    try:
        return max(1, min(int(value or default), ceiling))
    except (TypeError, ValueError):
        return default


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _error(exc: Exception) -> str:
    if isinstance(exc, LangfuseError):
        return _dump({"error": str(exc)})
    return _dump({"error": f"{type(exc).__name__}: {exc}"})


def _trace_summary(t: dict, io_chars: int, host: str) -> dict:
    out = {
        "id": t.get("id"),
        "name": t.get("name") or "",
        "timestamp": t.get("timestamp"),
        "sessionId": t.get("sessionId"),
        "userId": t.get("userId"),
        "tags": t.get("tags") or [],
        "latency_s": t.get("latency"),
        "cost_usd": t.get("totalCost"),
        "input": _cap(t.get("input"), io_chars),
        "output": _cap(t.get("output"), io_chars),
    }
    if t.get("htmlPath"):
        out["url"] = f"{host}{t['htmlPath']}"
    return {k: v for k, v in out.items() if v not in (None, "", [])}


def _observation_line(o: dict) -> str:
    bits = [f"[{o.get('type')}] {o.get('name') or '(unnamed)'}"]
    if o.get("level") and o.get("level") != "DEFAULT":
        bits.append(f"level={o['level']}")
    if o.get("latency") is not None:
        bits.append(f"{o['latency']:.2f}s" if isinstance(o.get("latency"), (int, float)) else f"{o['latency']}s")
    if o.get("model"):
        bits.append(f"model={o['model']}")
    usage = o.get("usageDetails") or o.get("usage") or {}
    total = usage.get("total") if isinstance(usage, dict) else None
    if total:
        bits.append(f"tokens={total}")
    cost = o.get("calculatedTotalCost") or (o.get("costDetails") or {}).get("total")
    if cost:
        bits.append(f"cost=${cost:.4f}" if isinstance(cost, (int, float)) else f"cost={cost}")
    if o.get("statusMessage"):
        bits.append(f"status={str(o['statusMessage'])[:120]}")
    bits.append(f"id={o.get('id')}")
    return " · ".join(bits)


def observation_tree(observations: list[dict], max_nodes: int) -> dict:
    """An indented tree of a trace's observations, with the structural health checks
    that matter when debugging instrumentation: how many roots, and which spans point
    at a parent that is not in the trace (an orphan: its parent was never exported, or
    the run is still in flight — a span is exported when it ENDS)."""
    ids = {o.get("id") for o in observations}
    children: dict[str | None, list[dict]] = {}
    orphans = []
    for o in observations:
        parent = o.get("parentObservationId")
        if parent and parent not in ids:
            orphans.append(o)
            parent = None  # render orphans at the top level, flagged
        children.setdefault(parent, []).append(o)
    for group in children.values():
        group.sort(key=lambda o: o.get("startTime") or "")
    lines: list[str] = []
    orphan_ids = {o.get("id") for o in orphans}

    def walk(parent: str | None, depth: int) -> None:
        for o in children.get(parent, []):
            if len(lines) >= max_nodes:
                return
            flag = "ORPHAN " if o.get("id") in orphan_ids else ""
            lines.append(f"{'  ' * depth}- {flag}{_observation_line(o)}")
            walk(o.get("id"), depth + 1)

    walk(None, 0)
    roots = [o for o in observations if not o.get("parentObservationId")]
    return {
        "observations": len(observations),
        "roots": len(roots),
        "orphans": len(orphans),
        "orphan_missing_parents": sorted({o.get("parentObservationId") for o in orphans}),
        "tree": lines,
        "truncated": len(observations) > len(lines),
    }


def _search_results(payload: dict) -> list[dict]:
    """Normalise a ``/api/search-docs`` reply. ``answer`` is a JSON string holding either a
    list of documents (the shape the skill documents) or ``{"content": [documents]}`` (the
    shape the live endpoint returns). Each document's excerpts are strings or
    ``{"type": "text", "text": ...}`` blocks."""
    answer = payload.get("answer")
    docs = json.loads(answer) if isinstance(answer, str) else answer
    if isinstance(docs, dict):
        docs = docs.get("content") or []
    results = []
    for d in (docs or [])[:8]:
        chunks = (d.get("source") or {}).get("content") or []
        text = " … ".join(c.get("text", "") if isinstance(c, dict) else str(c) for c in chunks)
        results.append({"title": d.get("title"), "url": d.get("url"), "excerpt": _cap(text, 600)})
    return results


def build_tools(
    cfg: dict, host_config: Callable[[], Any] | None = None, *, transport: httpx.BaseTransport | None = None
) -> list:
    """The read tools, plus the write tools when ``allow_writes`` is on."""
    client = LangfuseClient(cfg, host_config, transport=transport)
    io_chars = _limit(cfg.get("max_io_chars"), 1500, ceiling=20_000)
    allow_writes = bool(cfg.get("allow_writes"))

    def host() -> str:
        return client.credentials().host

    @tool
    def langfuse_traces(
        limit: int = 20,
        name: str = "",
        session_id: str = "",
        user_id: str = "",
        tags: list[str] | None = None,
        since_hours: float = 0,
        order_by: str = "",
        environment: str = "",
    ) -> str:
        """List recent Langfuse traces (newest first), with capped input/output and a UI link.

        Filter by trace name, session_id, user_id, tags (all must match), environment, or a
        time window (since_hours). order_by is "<field>.<asc|desc>", e.g. "latency.desc" or
        "totalCost.desc". Use langfuse_trace for one trace's full observation tree.
        """
        try:
            data = client.get(
                "/api/public/traces",
                limit=_limit(limit, 20),
                name=name,
                sessionId=session_id,
                userId=user_id,
                tags=tags or None,
                fromTimestamp=_since(since_hours),
                orderBy=order_by,
                environment=environment,
            )
            base = host()
            return _dump(
                {
                    "total": (data.get("meta") or {}).get("totalItems"),
                    "traces": [_trace_summary(t, io_chars, base) for t in data.get("data") or []],
                }
            )
        except Exception as exc:  # never raise into the turn
            return _error(exc)

    @tool
    def langfuse_trace(trace_id: str, max_observations: int = 150, include_io: bool = True) -> str:
        """Get one Langfuse trace: its summary, capped input/output, scores, and an indented
        tree of its observations (spans, generations, tools) with latency, model, tokens,
        cost and level. The tree reports how many roots the trace has and flags ORPHAN
        observations whose parent is missing from the trace — usually a parent span that
        was never exported, or a run still in flight (a span is exported when it ends).
        """
        try:
            t = client.get(f"/api/public/traces/{trace_id}")
            summary = _trace_summary(t, io_chars if include_io else 0, host())
            if not include_io:
                summary.pop("input", None)
                summary.pop("output", None)
            tree = observation_tree(t.get("observations") or [], _limit(max_observations, 150, ceiling=1000))
            scores = [
                {k: s.get(k) for k in ("name", "value", "stringValue", "source", "comment") if s.get(k) is not None}
                for s in t.get("scores") or []
            ]
            return _dump({**summary, "scores": scores, **tree})
        except Exception as exc:  # never raise into the turn
            return _error(exc)

    @tool
    def langfuse_observations(
        trace_id: str = "",
        type: str = "",
        name: str = "",
        user_id: str = "",
        since_hours: float = 0,
        limit: int = 30,
        include_io: bool = False,
    ) -> str:
        """List Langfuse observations across traces — e.g. every GENERATION for a model call
        name, every TOOL span named "tool:web_search", or all observations of one trace.

        type is SPAN | GENERATION | EVENT | AGENT | TOOL | CHAIN | RETRIEVER | EMBEDDING.
        IO is omitted unless include_io is true (then capped).
        """
        try:
            data = client.get(
                "/api/public/observations",
                traceId=trace_id,
                type=type.upper() if type else "",
                name=name,
                userId=user_id,
                fromStartTime=_since(since_hours),
                limit=_limit(limit, 30),
            )
            rows = []
            for o in data.get("data") or []:
                row = {
                    "line": _observation_line(o),
                    "traceId": o.get("traceId"),
                    "startTime": o.get("startTime"),
                    "parentObservationId": o.get("parentObservationId"),
                }
                if include_io:
                    row["input"] = _cap(o.get("input"), io_chars)
                    row["output"] = _cap(o.get("output"), io_chars)
                rows.append(row)
            return _dump({"total": (data.get("meta") or {}).get("totalItems"), "observations": rows})
        except Exception as exc:  # never raise into the turn
            return _error(exc)

    @tool
    def langfuse_sessions(session_id: str = "", limit: int = 20, since_hours: float = 0) -> str:
        """List Langfuse sessions (grouped conversations), or with session_id, get that
        session's traces in order with their capped input/output."""
        try:
            if session_id:
                s = client.get(f"/api/public/sessions/{session_id}")
                base = host()
                traces = sorted(s.get("traces") or [], key=lambda t: t.get("timestamp") or "")
                return _dump(
                    {
                        "id": s.get("id"),
                        "createdAt": s.get("createdAt"),
                        "traces": [_trace_summary(t, io_chars, base) for t in traces],
                    }
                )
            data = client.get("/api/public/sessions", limit=_limit(limit, 20), fromTimestamp=_since(since_hours))
            return _dump(
                {
                    "total": (data.get("meta") or {}).get("totalItems"),
                    "sessions": [{"id": s.get("id"), "createdAt": s.get("createdAt")} for s in data.get("data") or []],
                }
            )
        except Exception as exc:  # never raise into the turn
            return _error(exc)

    @tool
    def langfuse_scores(
        trace_id: str = "", name: str = "", source: str = "", since_hours: float = 0, limit: int = 30
    ) -> str:
        """List Langfuse scores (evaluations, user feedback, annotations) — filter by trace,
        score name, source (API | EVAL | ANNOTATION) or time window."""
        try:
            params = {
                "traceId": trace_id,
                "name": name,
                "source": source.upper() if source else "",
                "fromTimestamp": _since(since_hours),
                "limit": _limit(limit, 30),
            }
            try:
                data = client.get("/api/public/v2/scores", **params)
            except LangfuseError as exc:
                if "404" not in str(exc):
                    raise
                data = client.get("/api/public/scores", **params)  # older servers
            keep = ("id", "traceId", "observationId", "name", "value", "stringValue", "dataType", "source", "comment")
            return _dump(
                {
                    "total": (data.get("meta") or {}).get("totalItems"),
                    "scores": [{k: s.get(k) for k in keep if s.get(k) is not None} for s in data.get("data") or []],
                }
            )
        except Exception as exc:  # never raise into the turn
            return _error(exc)

    @tool
    def langfuse_prompts(name: str = "", label: str = "", version: int = 0) -> str:
        """List prompts in Langfuse prompt management, or with name, get one prompt's
        content and config (the version with `label`, default "production", or a specific
        version)."""
        try:
            if name:
                p = client.get(
                    f"/api/public/v2/prompts/{name}",
                    label=label if not version else "",
                    version=version or None,
                )
                keep = ("name", "version", "type", "labels", "tags", "config", "prompt", "commitMessage")
                return _dump({k: p.get(k) for k in keep if p.get(k) is not None})
            data = client.get("/api/public/v2/prompts", label=label, limit=100)
            return _dump(
                {
                    "prompts": [
                        {k: p.get(k) for k in ("name", "versions", "labels", "tags", "lastUpdatedAt") if p.get(k)}
                        for p in data.get("data") or []
                    ]
                }
            )
        except Exception as exc:  # never raise into the turn
            return _error(exc)

    @tool
    def langfuse_datasets(name: str = "", limit: int = 30) -> str:
        """List Langfuse datasets, or with name, list that dataset's items (capped
        input / expected output, and the trace each item came from)."""
        try:
            if name:
                data = client.get("/api/public/dataset-items", datasetName=name, limit=_limit(limit, 30))
                items = [
                    {
                        "id": i.get("id"),
                        "status": i.get("status"),
                        "input": _cap(i.get("input"), io_chars),
                        "expectedOutput": _cap(i.get("expectedOutput"), io_chars),
                        "sourceTraceId": i.get("sourceTraceId"),
                    }
                    for i in data.get("data") or []
                ]
                return _dump({"dataset": name, "total": (data.get("meta") or {}).get("totalItems"), "items": items})
            data = client.get("/api/public/v2/datasets", limit=_limit(limit, 30))
            keep = ("name", "description", "createdAt", "updatedAt")
            return _dump({"datasets": [{k: d.get(k) for k in keep if d.get(k)} for d in data.get("data") or []]})
        except Exception as exc:  # never raise into the turn
            return _error(exc)

    @tool
    def langfuse_metrics(days: int = 7, trace_name: str = "", user_id: str = "", tags: list[str] | None = None) -> str:
        """Daily Langfuse usage: trace and observation counts, cost, and token usage per
        model, for the last `days` days (optionally for one trace name, user or tag set)."""
        try:
            data = client.get(
                "/api/public/metrics/daily",
                traceName=trace_name,
                userId=user_id,
                tags=tags or None,
                fromTimestamp=_since(_limit(days, 7, ceiling=90) * 24),
                limit=_limit(days, 7, ceiling=90),
            )
            return _dump({"days": data.get("data") or []})
        except Exception as exc:  # never raise into the turn
            return _error(exc)

    @tool
    def langfuse_api(method: str, path: str, params: dict | None = None, body: dict | None = None) -> str:
        """Call any Langfuse public API endpoint directly, for what the other langfuse_*
        tools don't cover. path must start with /api/public/ (see the API reference via
        langfuse_docs). GET is always allowed; POST/PATCH/PUT/DELETE only when the plugin's
        allow_writes setting is on. The response is returned as JSON, capped.
        """
        if method.upper() != "GET" and not allow_writes:
            return _dump({"error": "writes are disabled — the operator must set langfuse.allow_writes: true"})
        try:
            data = client.request(method, path, params=params, body=body)
            return _cap(_dump(data), max(io_chars * 8, 8000))
        except Exception as exc:  # never raise into the turn
            return _error(exc)

    @tool
    def langfuse_docs(query: str = "", page: str = "") -> str:
        """Read the current Langfuse documentation (it changes often — check it before
        writing Langfuse code). With page (a path like "docs/observability/overview" or a
        langfuse.com URL), return that page as markdown. With query, search the docs and
        GitHub issues/discussions. With neither, return the docs index (llms.txt) to find
        the right page.
        """
        try:
            with httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT}, transport=transport) as http:
                if page:
                    path = re.sub(r"^https?://(www\.)?langfuse\.com", "", page.strip()).strip("/")
                    if "://" in path:
                        return _dump({"error": "page must be a langfuse.com path or URL"})
                    path = path[:-3] if path.endswith(".md") else path
                    resp = http.get(f"{DOCS_HOST}/{path}.md")
                elif query:
                    resp = http.get(f"{DOCS_HOST}/api/search-docs", params={"query": query})
                else:
                    resp = http.get(f"{DOCS_HOST}/llms.txt")
            if resp.status_code >= 400:
                return _dump({"error": f"langfuse.com returned {resp.status_code}"})
            if query and not page:
                try:
                    return _dump({"query": query, "results": _search_results(resp.json())})
                except Exception:  # an unexpected shape: fall through to the raw (capped) text
                    pass
            return _cap(resp.text, _MAX_DOC_CHARS)
        except httpx.HTTPError as exc:
            return _dump({"error": f"could not reach langfuse.com: {type(exc).__name__}"})

    @tool
    def langfuse_reference(name: str = "") -> str:
        """Read one of the Langfuse skill's use-case references (the langfuse skill names
        which one fits: instrumentation, create-dataset, prompt-migration,
        prompt-engineering, setting-up-evals, user-feedback, error-analysis,
        judge-calibration, ci-cd, cli, v4-project-migration, skill-feedback). With no name,
        list them.
        """
        available = sorted(p.stem for p in REFERENCES_DIR.glob("*.md"))
        key = (name or "").strip().removesuffix(".md").removeprefix("references/")
        if not key:
            return _dump({"references": available})
        if key not in available:
            return _dump({"error": f"no reference {name!r}", "references": available})
        return (REFERENCES_DIR / f"{key}.md").read_text(encoding="utf-8")

    tools = [
        langfuse_traces,
        langfuse_trace,
        langfuse_observations,
        langfuse_sessions,
        langfuse_scores,
        langfuse_prompts,
        langfuse_datasets,
        langfuse_metrics,
        langfuse_api,
        langfuse_docs,
        langfuse_reference,
    ]
    if allow_writes:
        tools += _write_tools(client)
    return tools


def _write_tools(client: LangfuseClient) -> list:
    @tool
    def langfuse_score(
        trace_id: str,
        name: str,
        value: float | str,
        observation_id: str = "",
        comment: str = "",
        data_type: str = "",
    ) -> str:
        """Attach a score to a Langfuse trace (or one of its observations): an evaluation
        result, a label, or feedback. value is a number, or a string for CATEGORICAL.
        data_type is NUMERIC | CATEGORICAL | BOOLEAN (inferred when empty)."""
        body = {
            "traceId": trace_id,
            "observationId": observation_id or None,
            "name": name,
            "value": value,
            "comment": comment or None,
            "dataType": data_type.upper() or None,
        }
        try:
            return _dump(client.post("/api/public/scores", {k: v for k, v in body.items() if v is not None}))
        except Exception as exc:  # never raise into the turn
            return _error(exc)

    @tool
    def langfuse_dataset_item(
        dataset_name: str,
        input: Any,
        expected_output: Any = None,
        metadata: dict | None = None,
        source_trace_id: str = "",
        source_observation_id: str = "",
        create_dataset: bool = False,
        dataset_description: str = "",
    ) -> str:
        """Add an item to a Langfuse dataset — e.g. turn a failing production trace into a
        regression case (pass source_trace_id). With create_dataset, create the dataset
        first if it does not exist."""
        try:
            if create_dataset:
                try:
                    client.get(f"/api/public/v2/datasets/{dataset_name}")
                except LangfuseError as exc:
                    if "404" not in str(exc):
                        raise
                    client.post("/api/public/v2/datasets", {"name": dataset_name, "description": dataset_description})
            body = {
                "datasetName": dataset_name,
                "input": input,
                "expectedOutput": expected_output,
                "metadata": metadata,
                "sourceTraceId": source_trace_id or None,
                "sourceObservationId": source_observation_id or None,
            }
            return _dump(client.post("/api/public/dataset-items", {k: v for k, v in body.items() if v is not None}))
        except Exception as exc:  # never raise into the turn
            return _error(exc)

    return [langfuse_score, langfuse_dataset_item]
