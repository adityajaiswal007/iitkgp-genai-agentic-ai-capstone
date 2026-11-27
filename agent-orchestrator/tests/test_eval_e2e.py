"""Grading and failure classification of the end-to-end evaluation (no agent or model needed)."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_e2e.py"
spec = importlib.util.spec_from_file_location("eval_e2e", SCRIPT)
e2e = importlib.util.module_from_spec(spec)
spec.loader.exec_module(e2e)


def item(**overrides):
    base = {"set": "TAX", "id": "T1", "category": "circular_notification", "question": "What are the limits?",
            "expected": "answer", "reference": "ITAT Rs 50 lakh; High Court Rs 1 crore.",
            "key_facts": ["ITAT limit Rs 50 lakh", "High Court limit Rs 1 crore"], "evidence": [],
            "must_not_contain": [], "targets": [{"document_id": "tax-circular-17-2019", "pages": [1]}]}
    base.update(overrides)
    return base


def raw(status="answered", text="The ITAT limit is ₹50,00,000 and the High Court limit is ₹1,00,00,000.", **extra):
    record = {"id": "T1", "status": status, "answer_text": text, "seconds": 9.5,
              "sources": [{"document_id": "tax-circular-17-2019", "page": 1}],
              "metrics": {"tokens_charged": 9000, "revisions": 0}, "trace": [], "trace_id": "abc"}
    record.update(extra)
    return record


def test_gold_sets_load_with_corrected_rbi_questions():
    items = e2e.load_gold({"rbi", "tax"})
    assert len(items) == 124
    rbi = [i for i in items if i["set"] == "RBI"]
    assert all(i["expected"] == "answer" for i in rbi)  # Q29/Q30 corrected to lookups
    tax = [i["expected"] for i in items if i["set"] == "TAX"]
    assert (tax.count("answer"), tax.count("refuse"), tax.count("clarify"), tax.count("abstain")) == (74, 16, 2, 2)
    assert all(t["document_id"] for i in items for t in i["targets"])  # every gold file maps to a catalog ID


def test_key_facts_match_across_lakh_and_digit_forms():
    assert e2e.fact_present("ITAT limit Rs 50 lakh", "The ITAT limit is ₹50,00,000.")
    assert not e2e.fact_present("ITAT limit Rs 50 lakh", "The ITAT limit is ₹60,00,000.")
    graded = e2e.deterministic_grade(item(), "The ITAT limit is ₹50,00,000; the High Court limit is ₹1 crore.")
    assert graded["verdict"] == "correct" and graded["key_fact_recall"] == 1.0


def test_a_fully_correct_answer_passes():
    row = e2e.grade(item(), raw(), None, None)
    assert row["passed"] and row["decision_correct"] and row["gold_doc_cited"] and row["gold_page_cited"]


@pytest.mark.parametrize("status,expected,stop,category", [
    ("refused", "answer", None, "over_refusal"),
    ("clarify", "answer", None, "unnecessary_clarification"),
    ("abstained", "answer", "TOKEN_CAP", "abstained_system"),
    ("abstained", "answer", "INSUFFICIENT_EVIDENCE", "abstained_verification"),
    ("answered", "refuse", None, "under_refusal"),
    ("clarify", "refuse", None, "decision_mismatch"),
    ("abstained", "abstain", "INSUFFICIENT_EVIDENCE", None),
])
def test_decision_failures_are_classified(status, expected, stop, category):
    trace = [{"kind": "control", "name": "stop", "code": stop}] if stop else []
    row = e2e.grade(item(expected=expected), raw(status=status, trace=trace), None, None)
    assert row["failure_category"] == (category or "")


def test_abstention_is_blamed_on_retrieval_when_the_evidence_was_never_found():
    trace = [{"kind": "control", "name": "stop", "code": "INSUFFICIENT_EVIDENCE"}]
    row = e2e.grade(item(), raw(status="abstained", trace=trace), None, {"evidence_hit": "0"})
    assert row["failure_category"] == "abstained_retrieval_miss"


def test_answer_quality_failures():
    wrong_doc = raw(sources=[{"document_id": "tax-circular-3-2018", "page": 1}], text="The limit is ₹20 lakh.")
    assert e2e.grade(item(), wrong_doc, None, None)["failure_category"] == "wrong_source"
    partial = raw(text="The ITAT limit is ₹50 lakh.")
    assert e2e.grade(item(), partial, None, None)["failure_category"] == "incomplete_answer"
    invented = raw(text="Section 999 provides an incentive of ₹5 lakh.")
    row = e2e.grade(item(must_not_contain=["section 999 provides"]), invented, None, None)
    assert row["failure_category"] == "forbidden_content"


def test_llm_judge_verdict_takes_precedence_and_bad_output_falls_back():
    class FakeJudge(e2e.Judge):
        def __init__(self, reply):
            message = SimpleNamespace(content=reply)
            create = lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=message)])
            self.qwen = SimpleNamespace(disable_thinking=False, model="m",
                                        client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))

    reply = '<think>checking</think>{"verdict": "incorrect", "missing_facts": [], "wrong_facts": ["1 crore"], "reason": "wrong HC limit"}'
    row = e2e.grade(item(), raw(), FakeJudge(reply), None)
    assert row["verdict"] == "incorrect" and row["failure_category"] == "incorrect_answer"
    assert "wrong HC limit" in row["judge_reason"]
    fallback = e2e.grade(item(), raw(), FakeJudge("not json"), None)
    assert fallback["judge_verdict"] is None and fallback["verdict"] == "correct"  # deterministic grade used


def test_summary_and_failure_report_render():
    rows = [e2e.grade(item(), raw(), None, None),
            e2e.grade(item(id="T2", expected="refuse"), raw(), None, None)]
    summary = e2e.summarise(rows, "t", False, "qwen")
    assert "| All | 2 | 50% |" in summary and "under_refusal" in summary
    report = e2e.failures_report(rows, lambda trace_id: f"https://lf/{trace_id}")
    assert "## T2" in report and "https://lf/abc" in report and "## T1" not in report
