"""Langfuse tracing: spans are captured in memory, so no Langfuse server is needed."""
import json

import pytest
from fastapi.testclient import TestClient

langfuse = pytest.importorskip("langfuse")
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

from regulatory_agent import tracing  # noqa: E402
from regulatory_agent.agent import RegulatoryAgent  # noqa: E402
from regulatory_agent.api import create_app  # noqa: E402
from regulatory_agent.config import Settings  # noqa: E402
from regulatory_agent.contracts import Limits  # noqa: E402
from regulatory_agent.models import REVIEW_FIELDS, ScriptedModel  # noqa: E402
from regulatory_agent.tools import DEMO_DOCUMENTS, CorpusTools  # noqa: E402


@pytest.fixture(scope="module")
def _shared_client():
    # Langfuse clients are shared per public key, so the whole module uses one exporter.
    exp = InMemorySpanExporter()
    client = langfuse.Langfuse(public_key="pk-lf-test", secret_key="sk-lf-test",
                               base_url="http://127.0.0.1:9", span_exporter=exp)
    yield exp, client


@pytest.fixture()
def exporter(_shared_client, monkeypatch):
    exp, client = _shared_client
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    client.flush()
    exp.clear()
    tracing.set_client(client)
    yield exp, client
    client.flush()
    tracing.set_client(None)


def spans_by_name(exp, client):
    client.flush()
    spans = exp.get_finished_spans()
    return spans, {s.name: s for s in spans}


def test_one_question_is_one_nested_trace_with_a_session(exporter, tmp_path):
    exp, client = exporter
    with TestClient(create_app(Settings(mode="demo", database_path=tmp_path / "s.sqlite"))) as c:
        body = c.post("/v1/executions", json={"question": "What does the CBDT circular say about departmental appeals?",
                                              "conversation_id": "conv-42"}).json()
    assert body["status"] == "answered"
    spans, named = spans_by_name(exp, client)
    assert len({s.context.trace_id for s in spans}) == 1
    root = named["regassist-query"]
    assert root.parent is None
    for child in ("resolve_context", "planner", "executor", "critic", "finish"):
        assert named[child].parent.span_id == root.context.span_id
    # model and tool calls run in worker threads but still nest under their node
    assert named["qwen.planner"].parent.span_id == named["planner"].context.span_id
    assert named["tool.search_documents"].parent.span_id == named["executor"].context.span_id
    assert named["tool.search_documents"].attributes["langfuse.observation.type"] == "retriever"
    assert named["qwen.executor"].attributes["langfuse.observation.type"] == "generation"
    assert all(s.attributes.get("session.id") == "conv-42" for s in spans)


def test_trace_shows_the_exact_reason_an_answer_was_rejected(exporter, tmp_path):
    exp, client = exporter
    checks = dict.fromkeys(REVIEW_FIELDS, "pass")
    bad = {"claims": [{"text": "The deadline is 24 hours.", "citations": ["S1"]}]}
    model = ScriptedModel({"executor": [bad, bad], "critic": [{"decision": "accept", "checks": checks}] * 2})
    agent = RegulatoryAgent(model, CorpusTools(DEMO_DOCUMENTS), tmp_path / "s.sqlite", Limits())
    try:
        with tracing.observation("regassist-query", as_type="agent"):
            state = agent.ask("conv-7", "RBI complaint workflow")
    finally:
        agent.close()
    assert state["status"] == "abstained"
    spans, _ = spans_by_name(exp, client)
    verdicts = [s for s in spans if s.name == "critic.verdict"]
    assert verdicts
    output = json.loads(verdicts[0].attributes["langfuse.observation.output"])
    assert any("number absent" in issue for issue in output["automatic_check_issues"])
    stop = next(s for s in spans if s.name == "control.stop")
    assert stop.attributes["langfuse.observation.level"] == "WARNING"
    assert "INSUFFICIENT_EVIDENCE" in stop.attributes["langfuse.observation.output"]


def test_tracing_is_a_no_op_without_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    tracing.set_client(None)
    assert not tracing.enabled() and tracing.client() is None
    with tracing.observation("anything") as obs:
        obs.update(output="ignored")
    tracing.event("ignored")
    with TestClient(create_app(Settings(mode="demo", database_path=tmp_path / "s.sqlite"))) as c:
        assert c.post("/v1/executions", json={"question": "RBI complaint workflow"}).json()["status"] == "answered"


def test_long_payloads_are_clipped_for_the_dashboard():
    clipped = tracing.clip({"text": "x" * 5000, "items": list(range(80))})
    assert len(clipped["text"]) < 4100 and clipped["text"].endswith("more characters]")
    assert len(clipped["items"]) == 50


def test_public_traces_flag_publishes_the_trace_and_returns_its_id(exporter, monkeypatch, tmp_path):
    exp, client = exporter
    monkeypatch.setenv("LANGFUSE_PUBLIC_TRACES", "1")
    monkeypatch.setattr(tracing, "trace_url", lambda trace_id: None)  # no Langfuse server here
    with TestClient(create_app(Settings(mode="demo", database_path=tmp_path / "s.sqlite"))) as c:
        response = c.post("/v1/executions", json={"question": "RBI complaint workflow"})
    _, named = spans_by_name(exp, client)
    root = named["regassist-query"]
    assert response.headers["X-Trace-Id"] == format(root.context.trace_id, "032x")
    assert root.attributes.get("langfuse.trace.public") is True


def test_traces_are_private_by_default(exporter, monkeypatch, tmp_path):
    exp, client = exporter
    monkeypatch.delenv("LANGFUSE_PUBLIC_TRACES", raising=False)
    with TestClient(create_app(Settings(mode="demo", database_path=tmp_path / "s.sqlite"))) as c:
        response = c.post("/v1/executions", json={"question": "RBI complaint workflow"})
    _, named = spans_by_name(exp, client)
    assert "X-Trace-Id" in response.headers
    assert not named["regassist-query"].attributes.get("langfuse.trace.public")


def test_showcase_script_lists_its_questions():
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "scripts/trace_showcase.py", "--dry-run"],
                         capture_output=True, text=True, check=True).stdout
    assert out.count("\n") == 10 and "Prompt-injection" in out and "expect: refused" in out
