from __future__ import annotations

import json

from conftest import CFG
from langfuse_plugin.tools import build_tools, observation_tree


def _tools(fake, **cfg):
    return {t.name: t for t in build_tools({**CFG, **cfg}, transport=fake.transport)}


def test_every_tool_has_a_description_and_a_unique_prefixed_name(fake):
    tools = build_tools({**CFG, "allow_writes": True}, transport=fake.transport)
    names = [t.name for t in tools]
    assert len(names) == len(set(names)) and all(n.startswith("langfuse_") for n in names)
    assert all((t.description or "").strip() for t in tools)


def test_write_tools_exist_only_with_allow_writes(fake):
    assert {"langfuse_score", "langfuse_dataset_item"}.isdisjoint(_tools(fake))
    assert {"langfuse_score", "langfuse_dataset_item"} <= set(_tools(fake, allow_writes=True))


def test_traces_are_summarised_with_capped_io_and_a_ui_link(fake):
    fake.on(
        "GET",
        "/api/public/traces",
        {
            "data": [
                {"id": "t1", "name": "chat", "input": "x" * 5000, "output": "ok", "htmlPath": "/project/p/traces/t1"}
            ],
            "meta": {"totalItems": 1},
        },
    )
    out = json.loads(_tools(fake, max_io_chars=100)["langfuse_traces"].invoke({"limit": 5, "since_hours": 2}))

    (t,) = out["traces"]
    assert t["url"] == "https://lf.example/project/p/traces/t1"
    assert t["input"].startswith("x" * 100) and "more chars" in t["input"]
    assert "fromTimestamp" in dict(fake.requests[-1].url.params)


def test_observation_tree_flags_orphans_and_counts_roots():
    obs = [
        {"id": "root", "type": "SPAN", "name": "a2a-stream", "startTime": "1"},
        {"id": "g1", "type": "GENERATION", "name": "turn", "parentObservationId": "root", "startTime": "2"},
        {"id": "o1", "type": "TOOL", "name": "tool:x", "parentObservationId": "never-exported", "startTime": "3"},
    ]
    tree = observation_tree(obs, max_nodes=50)

    assert (tree["roots"], tree["orphans"]) == (1, 1)
    assert tree["orphan_missing_parents"] == ["never-exported"]
    assert tree["tree"][0].startswith("- [SPAN] a2a-stream")
    assert tree["tree"][1].startswith("  - [GENERATION] turn")
    assert any(line.startswith("- ORPHAN [TOOL] tool:x") for line in tree["tree"])


def test_trace_tool_returns_the_tree_and_scores(fake):
    fake.on(
        "GET",
        "/api/public/traces/t1",
        {
            "id": "t1",
            "name": "chat",
            "observations": [{"id": "r", "type": "SPAN", "name": "chat", "latency": 1.5}],
            "scores": [{"name": "helpful", "value": 1, "source": "API"}],
        },
    )
    out = json.loads(_tools(fake)["langfuse_trace"].invoke({"trace_id": "t1"}))

    assert out["roots"] == 1 and out["orphans"] == 0
    assert out["scores"] == [{"name": "helpful", "value": 1, "source": "API"}]
    assert "1.50s" in out["tree"][0]


def test_scores_fall_back_through_v2_to_v1_on_older_servers(fake):
    fake.on("GET", "/api/public/scores", {"data": [{"name": "q", "value": 0.5}], "meta": {"totalItems": 1}})
    out = json.loads(_tools(fake)["langfuse_scores"].invoke({"trace_id": "t1"}))
    assert out["scores"] == [{"name": "q", "value": 0.5}]
    assert [r.url.path for r in fake.requests] == [
        "/api/public/v3/scores",
        "/api/public/v2/scores",
        "/api/public/scores",
    ]


def test_the_api_escape_hatch_refuses_writes_unless_allowed(fake):
    fake.on("POST", "/api/public/scores", {"id": "s1"})
    refused = json.loads(_tools(fake)["langfuse_api"].invoke({"method": "POST", "path": "/api/public/scores"}))
    assert "writes are disabled" in refused["error"]
    assert not fake.requests

    ok = _tools(fake, allow_writes=True)["langfuse_api"].invoke(
        {"method": "POST", "path": "/api/public/scores", "body": {"a": 1}}
    )
    assert json.loads(ok) == {"id": "s1"}


def test_errors_come_back_as_json_not_exceptions(fake):
    out = json.loads(_tools(fake)["langfuse_trace"].invoke({"trace_id": "missing"}))
    assert "404" in out["error"]


def test_score_tool_posts_only_the_fields_given(fake):
    fake.on("POST", "/api/public/scores", {"id": "s1"})
    _tools(fake, allow_writes=True)["langfuse_score"].invoke({"trace_id": "t1", "name": "correct", "value": 1})
    assert fake.last_json() == {"traceId": "t1", "name": "correct", "value": 1}


def test_dataset_item_can_create_its_dataset_first(fake):
    fake.on("POST", "/api/public/v2/datasets", {"name": "regressions"})
    fake.on("POST", "/api/public/dataset-items", {"id": "i1"})
    _tools(fake, allow_writes=True)["langfuse_dataset_item"].invoke(
        {"dataset_name": "regressions", "input": {"q": "hi"}, "source_trace_id": "t9", "create_dataset": True}
    )

    posted = [(r.method, r.url.path) for r in fake.requests if r.method == "POST"]
    assert posted == [("POST", "/api/public/v2/datasets"), ("POST", "/api/public/dataset-items")]
    assert fake.last_json() == {"datasetName": "regressions", "input": {"q": "hi"}, "sourceTraceId": "t9"}


def test_reference_lists_reads_and_refuses_traversal(fake):
    ref = _tools(fake)["langfuse_reference"]
    listed = json.loads(ref.invoke({}))["references"]
    assert "setting-up-evals" in listed and "instrumentation" in listed
    assert "# " in ref.invoke({"name": "references/setting-up-evals.md"})
    assert "error" in json.loads(ref.invoke({"name": "../../__init__"}))


def test_docs_refuses_a_non_langfuse_url(fake):
    out = json.loads(_tools(fake)["langfuse_docs"].invoke({"page": "https://evil.example/x"}))
    assert "langfuse.com" in out["error"]
    assert not fake.requests


def test_docs_search_handles_both_answer_shapes():
    from langfuse_plugin.tools import _search_results

    doc = {
        "title": "LangGraph",
        "url": "https://langfuse.com/x",
        "source": {"content": [{"type": "text", "text": "hi"}]},
    }
    live = {"answer": json.dumps({"content": [doc]})}
    documented = {"answer": json.dumps([{**doc, "source": {"content": ["hi"]}}])}

    for payload in (live, documented):
        assert _search_results(payload) == [{"title": "LangGraph", "url": "https://langfuse.com/x", "excerpt": "hi"}]


def test_an_unexpected_response_shape_is_an_error_not_a_crash(fake):
    fake.on("GET", "/api/public/traces", {"data": "not-a-list", "meta": {}})
    out = json.loads(_tools(fake)["langfuse_traces"].invoke({}))
    assert "error" in out


# ─── review fixes ─────────────────────────────────────────────────────────────────────


def test_path_traversal_never_leaves_the_public_api(fake):
    tools = _tools(fake, allow_writes=True)
    for path in (
        "/api/public/../admin/users",
        "/api/public/%2e%2e/admin",
        "/api/public/%252e%252e/x",
        "/api/public/a?b=1",
    ):
        out = json.loads(tools["langfuse_api"].invoke({"method": "GET", "path": path}))
        assert "error" in out, path
    # An id is encoded as ONE segment, and a decoded ".." inside it is still refused.
    out = json.loads(tools["langfuse_trace"].invoke({"trace_id": "../../admin/users"}))
    assert "'..'" in out["error"]
    assert not fake.requests  # nothing left the process


def test_folder_prompt_names_are_encoded_as_one_segment(fake):
    fake.on("GET", "/api/public/v2/prompts/folder/my-prompt", {"name": "folder/my-prompt", "version": 3})
    out = json.loads(_tools(fake)["langfuse_prompts"].invoke({"name": "folder/my-prompt", "label": "staging"}))
    req = fake.requests[-1]
    assert req.url.raw_path.startswith(b"/api/public/v2/prompts/folder%2Fmy-prompt")
    assert dict(req.url.params) == {"label": "staging"}
    assert out["version"] == 3


def test_allow_writes_is_parsed_strictly(fake):
    for off in ("false", "0", "no", "", None, False):
        assert "langfuse_score" not in _tools(fake, allow_writes=off), off
    for on in ("true", "YES", "1", True):
        assert "langfuse_score" in _tools(fake, allow_writes=on), on


def test_api_rejects_odd_methods_and_normalises_whitespace(fake):
    fake.on("GET", "/api/public/health", {"status": "OK"})
    tools = _tools(fake)
    assert json.loads(tools["langfuse_api"].invoke({"method": " get ", "path": "/api/public/health"})) == {
        "status": "OK"
    }
    assert (
        "unsupported"
        in json.loads(tools["langfuse_api"].invoke({"method": "HEAD", "path": "/api/public/health"}))["error"]
    )


def test_numeric_score_strings_are_sent_as_numbers(fake):
    fake.on("POST", "/api/public/scores", {"id": "s1"})
    score = _tools(fake, allow_writes=True)["langfuse_score"]
    score.invoke({"trace_id": "t", "name": "q", "value": "0.9", "data_type": "numeric"})
    assert fake.last_json() == {"traceId": "t", "name": "q", "value": 0.9, "dataType": "NUMERIC"}
    score.invoke({"trace_id": "t", "name": "label", "value": "7", "data_type": "CATEGORICAL"})
    assert fake.last_json()["value"] == "7"


def test_docs_never_raises_and_accepts_any_case_host(fake):
    docs = _tools(fake)["langfuse_docs"]
    assert "error" in json.loads(docs.invoke({"page": "docs\x00x"}))
    fake.on("GET", "/docs/x.md", "# x")
    assert docs.invoke({"page": "https://LANGFUSE.com/docs/x"}) == "# x"


def test_scores_prefer_the_v4_safe_v3_endpoint(fake):
    fake.on("GET", "/api/public/v3/scores", {"data": [{"name": "q", "value": 1}], "meta": {}})
    json.loads(_tools(fake)["langfuse_scores"].invoke({}))
    assert [r.url.path for r in fake.requests] == ["/api/public/v3/scores"]


def test_request_shapes_for_the_remaining_reads(fake):
    fake.on("GET", "/api/public/observations", {"data": [], "meta": {}})
    fake.on("GET", "/api/public/sessions/s/1", {"id": "s/1", "traces": []})
    fake.on("GET", "/api/public/dataset-items", {"data": [], "meta": {}})
    fake.on("GET", "/api/public/v2/datasets", {"data": [{"name": "d"}], "meta": {}})
    fake.on("GET", "/api/public/metrics/daily", {"data": [{"date": "2026-09-27"}], "meta": {}})
    t = _tools(fake)

    t["langfuse_observations"].invoke({"trace_id": "t1", "type": "generation", "name": "turn", "since_hours": 1})
    p = dict(fake.requests[-1].url.params)
    assert (p["traceId"], p["type"], p["name"]) == ("t1", "GENERATION", "turn") and "fromStartTime" in p

    assert json.loads(t["langfuse_sessions"].invoke({"session_id": "s/1"}))["id"] == "s/1"

    t["langfuse_datasets"].invoke({"name": "regressions"})
    assert dict(fake.requests[-1].url.params)["datasetName"] == "regressions"
    assert json.loads(t["langfuse_datasets"].invoke({}))["datasets"] == [{"name": "d"}]

    out = json.loads(t["langfuse_metrics"].invoke({"days": 3, "trace_name": "chat"}))
    p = dict(fake.requests[-1].url.params)
    assert out["days"] == [{"date": "2026-09-27"}] and p["traceName"] == "chat" and p["limit"] == "3"
