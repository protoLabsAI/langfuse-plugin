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


def test_scores_fall_back_to_v1_on_older_servers(fake):
    fake.on("GET", "/api/public/scores", {"data": [{"name": "q", "value": 0.5}], "meta": {"totalItems": 1}})
    out = json.loads(_tools(fake)["langfuse_scores"].invoke({"trace_id": "t1"}))
    assert out["scores"] == [{"name": "q", "value": 0.5}]


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
