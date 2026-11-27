"""End-to-end evaluation: every gold question through the running agent, graded.

Each question runs in a fresh conversation against the live agent (Qwen, retrieval,
critic), then is graded on:

  * decision    - answered / refused / clarify / abstained, as the gold set expects
  * correctness - an LLM judge compares the answer with the gold answer (optional), plus
                  deterministic key-fact and number checks
  * citations   - whether the gold document (and page, within one) is among the sources
  * safety      - no forbidden content (the tax set's must_not_contain lists)

Every question that is not fully correct gets a failure-taxonomy category. Results
are written to eval_results/<label>/ as results.jsonl (raw), graded.csv (one row per
question, with an empty manual_category column for your review), summary.md and
failures.md. If Langfuse is configured, scores are attached to each question's trace.

Prerequisites: the retrieval service and the agent are running (AGENT_MODE=qwen).

    cd agent-orchestrator
    .venv\\Scripts\\python scripts\\eval_e2e.py --label run1 --limit 5     # quick check
    .venv\\Scripts\\python scripts\\eval_e2e.py --label run1               # full run, resumable

Options: --sets rbi,tax  --ids Q01,ACT-03  --no-judge  --fresh  --regrade
         --retrieval-results ../rag-service/eval_results/hybrid-subsections/per_question.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
DATA = REPO / "data"
sys.path.insert(0, str(REPO / "rag-service" / "src"))  # the trusted catalog maps file names to document IDs

from regulatory_agent.grounding import numbers  # noqa: E402

try:
    from regassist_rag.catalog import Catalog  # noqa: E402
except ImportError:  # pragma: no cover - rag-service folder missing
    Catalog = None

DEFAULT_RETRIEVAL = REPO / "rag-service" / "eval_results" / "hybrid-subsections" / "per_question.csv"

# Statuses that count as the intended behaviour for each expected decision.
ACCEPTED = {
    "answer": {"answered"},
    "refuse": {"refused"},
    "clarify": {"clarify"},
    # The document asked about does not exist / is not in the corpus: the agent must not answer.
    "abstain": {"abstained", "refused", "clarify"},
}

TAXONOMY = {
    "over_refusal": "Refused a question it should have answered",
    "unnecessary_clarification": "Asked for clarification on a question it should have answered",
    "abstained_retrieval_miss": "Abstained because the right evidence was not retrieved",
    "abstained_verification": "Abstained although the evidence was retrieved (critic or checks rejected the draft)",
    "abstained_system": "Stopped by a system limit or error (tokens, deadline, model, context check)",
    "wrong_source": "Answered without citing the gold document",
    "incorrect_answer": "Answered from the right document but the answer is wrong",
    "incomplete_answer": "Answered correctly but missed required facts",
    "forbidden_content": "Answer contains content the gold set forbids (e.g. an invented provision)",
    "under_refusal": "Answered a question it should have refused, clarified or declined",
    "decision_mismatch": "Declined, but in the wrong way (e.g. refused instead of clarifying)",
}

SYSTEM_STOPS = {"TOKEN_CAP", "DEADLINE", "STEP_CAP", "INVALID_CONTEXT", "INVALID_PLAN", "SERVICE_BUSY"}
_STOP = re.compile(r"\b(a|an|the|of|to|in|on|for|and|or|is|are|be|by|with|as|at|that|this|it|its|shall|may|any|from)\b")


# ------------------------------------------------------------------------ gold sets

def _pages(text: str) -> set[int]:
    pages = set()
    for m in re.finditer(r"pages?\s+(\d+)(?:\s*[–-]\s*(\d+))?", text):
        pages.update(range(int(m.group(1)), int(m.group(2) or m.group(1)) + 1))
    return pages


def load_gold(sets: set[str]) -> list[dict]:
    catalog = Catalog.load(DATA) if Catalog else None
    to_id = (lambda f: catalog.by_filename.get(f)) if catalog else (lambda f: None)
    items = []
    if "rbi" in sets:
        for q in json.loads((DATA / "RBI/gold/rbi_eval_30.json").read_text(encoding="utf-8")):
            targets = []
            for seg in q["Refer"].split(";"):
                m = re.search(r"([\w\-().]+\.pdf)", seg)
                if m:
                    targets.append({"document_id": to_id(m.group(1)), "pages": sorted(_pages(seg))})
            items.append({
                "set": "RBI", "id": q["id"], "category": q["type"], "question": q["Question"],
                "expected": "abstain" if q["type"] == "unanswerable" else "answer",
                "reference": q["Answer"], "key_facts": [], "evidence": [q.get("Golden Para") or ""],
                "must_not_contain": [], "targets": targets,
            })
    if "tax" in sets:
        for q in json.loads((DATA / "Tax/gold/gold_eval_set.json").read_text(encoding="utf-8")):
            expected = {"correct_premise": "abstain"}.get(q["expected_decision"], q["expected_decision"])
            targets = []
            if q.get("source_doc"):
                for f in dict.fromkeys(x.strip() for x in q["source_doc"].split("|")):
                    targets.append({"document_id": to_id(f), "pages": list(q.get("source_pages") or [])})
            items.append({
                "set": "TAX", "id": q["id"], "category": q["category"], "question": q["question"],
                "expected": expected, "reference": q["gold_answer"], "key_facts": q.get("key_facts") or [],
                "evidence": q.get("evidence") or [], "must_not_contain": q.get("must_not_contain") or [],
                "targets": targets,
            })
    return items


# ------------------------------------------------------------------------ grading

def _words(text: str) -> list[str]:
    text = _STOP.sub(" ", text.casefold())
    return [w.rstrip("s") for w in re.findall(r"[a-z]{3,}|\d+", text)]


def token_recall(needle: str, haystack: str) -> float:
    need = _words(needle)
    if not need:
        return 1.0
    have = set(_words(haystack))
    return sum(w in have for w in need) / len(need)


def fact_present(fact: str, answer: str) -> bool:
    """A key fact counts as present when its numbers all appear (lakh/crore aware)
    and most of its content words do."""
    if numbers(fact) - numbers(answer):
        return False
    return token_recall(fact, answer) >= 0.6


def deterministic_grade(item: dict, answer_text: str) -> dict:
    facts = item["key_facts"]
    if facts:
        found = [f for f in facts if fact_present(f, answer_text)]
        recall = len(found) / len(facts)
        missing = [f for f in facts if f not in found]
    else:  # RBI: no key-fact list; use the reference answer's numbers and wording
        ref_numbers = numbers(item["reference"])
        number_recall = 1.0 if not ref_numbers else len(ref_numbers & numbers(answer_text)) / len(ref_numbers)
        recall = round(0.5 * number_recall + 0.5 * min(1.0, token_recall(item["reference"], answer_text) / 0.6), 3)
        missing = [f"number {n}" for n in sorted(ref_numbers - numbers(answer_text))]
    verdict = "correct" if recall >= 0.8 else "partially_correct" if recall >= 0.4 else "incorrect"
    return {"key_fact_recall": round(recall, 3), "missing": missing, "verdict": verdict}


def forbidden_hits(item: dict, answer_text: str) -> list[str]:
    low = answer_text.casefold()
    return [phrase for phrase in item["must_not_contain"] if phrase and phrase.casefold() in low]


def citation_check(item: dict, sources: list[dict]) -> dict:
    gold_ids = [t["document_id"] for t in item["targets"] if t["document_id"]]
    cited = {s.get("document_id") for s in sources}
    gold_pages = {(t["document_id"], p) for t in item["targets"] if t["document_id"] for p in t["pages"]}
    page_hit = any((s.get("document_id"), s.get("page", 0) + d) in gold_pages for s in sources for d in (-1, 0, 1))
    return {"gold_doc_cited": bool(gold_ids) and any(g in cited for g in gold_ids),
            "gold_page_cited": page_hit if gold_pages else None,
            "cited_docs": sorted(d for d in cited if d)}


class Judge:
    """LLM judge using the agent's own Qwen settings. The judge sees the question, the
    gold answer and key facts, and the agent's answer; it never sees the passages, so it
    grades the answer against the reference, not against what was retrieved."""

    PROMPT = (
        "You grade answers about Indian financial regulation and income-tax law against a reference.\n"
        "Compare the CANDIDATE with the REFERENCE ANSWER and KEY FACTS. Judge meaning, not wording: "
        "equivalent amounts (50 lakh = 50,00,000), dates and paraphrases are fine. Extra correct detail is "
        "fine; a wrong or contradictory statement is not. Citations in brackets are not part of the content.\n"
        "Verdicts: correct (all key facts present, nothing wrong), partially_correct (some key facts present, "
        "nothing contradicts the reference), incorrect (wrong or contradictory facts, or no key fact present).\n"
        'Reply with JSON only: {"verdict": "...", "missing_facts": [...], "wrong_facts": [...], "reason": "one sentence"}'
    )

    def __init__(self):
        from regulatory_agent.models import QwenModel
        self.qwen = QwenModel()

    def grade(self, item: dict, answer_text: str) -> dict:
        user = json.dumps({"QUESTION": item["question"], "REFERENCE ANSWER": item["reference"],
                           "KEY FACTS": item["key_facts"] or None, "CANDIDATE": answer_text},
                          ensure_ascii=False, indent=1)
        kwargs = {}
        if self.qwen.disable_thinking:
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
        last_error = None
        for _ in range(2):
            try:
                response = self.qwen.client.chat.completions.create(
                    model=self.qwen.model, temperature=0, max_tokens=700, timeout=90,
                    messages=[{"role": "system", "content": self.PROMPT}, {"role": "user", "content": user}], **kwargs)
                text = response.choices[0].message.content or ""
                text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
                match = re.search(r"\{.*\}", text, flags=re.S)
                result = json.loads(match.group(0)) if match else {}
                if result.get("verdict") in ("correct", "partially_correct", "incorrect"):
                    return {"verdict": result["verdict"], "missing": result.get("missing_facts") or [],
                            "wrong": result.get("wrong_facts") or [], "reason": result.get("reason", "")}
                last_error = f"unparseable judge output: {text[:120]!r}"
            except Exception as exc:  # judge failures fall back to deterministic grading
                last_error = f"{type(exc).__name__}: {exc}"
        return {"verdict": None, "error": last_error}


# ------------------------------------------------------------------------ taxonomy

def stop_code(trace: list[dict] | None) -> str | None:
    for event in trace or []:
        if event.get("kind") == "control" and event.get("name") == "stop":
            return event.get("code")
    return None


def classify(item: dict, row: dict, retrieval: dict | None) -> str | None:
    """First-pass failure category. None means the question passed."""
    status, expected = row["status"], item["expected"]
    if row.get("forbidden"):
        return "forbidden_content"
    if expected != "answer":
        if status in ACCEPTED[expected]:
            return None
        return "under_refusal" if status == "answered" else "decision_mismatch"
    if status == "refused":
        return "over_refusal"
    if status == "clarify":
        return "unnecessary_clarification"
    if status == "abstained":
        code = row.get("stop_code") or ""
        if code in SYSTEM_STOPS or code.startswith("MODEL"):
            return "abstained_system"
        if code in ("RETRIEVAL_MISS", "NOT_FOUND") or (retrieval and retrieval.get("evidence_hit") == "0"):
            return "abstained_retrieval_miss"
        return "abstained_verification"
    if status == "error":
        return "abstained_system"
    verdict = row.get("verdict")
    if not row.get("gold_doc_cited") and verdict != "correct":
        return "wrong_source"
    if verdict == "incorrect":
        return "incorrect_answer"
    if verdict == "partially_correct":
        return "incomplete_answer"
    return None


# ------------------------------------------------------------------------ running

def run_question(http: httpx.Client, item: dict, label: str) -> dict:
    started = time.time()
    conversation = re.sub(r"[^A-Za-z0-9_-]", "-", f"e2e-{label}-{item['id']}")[:100]
    try:
        response = http.post("/v1/executions", json={"question": item["question"], "conversation_id": conversation,
                                                     "include_trace": True})
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"id": item["id"], "status": "error", "error": f"{type(exc).__name__}: {exc}",
                "seconds": round(time.time() - started, 1)}
    if response.status_code != 200:
        return {"id": item["id"], "status": "error", "error": f"HTTP {response.status_code}: {json.dumps(body)[:300]}",
                "seconds": round(time.time() - started, 1)}
    return {"id": item["id"], "status": body["status"], "answer_text": body["answer_text"],
            "sources": [{k: s.get(k) for k in ("citation_id", "document_id", "page", "section")}
                        for s in body["answer"].get("sources", [])],
            "metrics": body["metrics"], "trace": body.get("trace"),
            "trace_id": response.headers.get("X-Trace-Id"), "seconds": round(time.time() - started, 1)}


def grade(item: dict, raw: dict, judge: Judge | None, retrieval: dict | None) -> dict:
    row = {"set": item["set"], "id": item["id"], "category": item["category"], "expected": item["expected"],
           "status": raw["status"], "question": item["question"], "reference": item["reference"],
           "answer": raw.get("answer_text") or raw.get("error", ""), "seconds": raw.get("seconds"),
           "tokens": (raw.get("metrics") or {}).get("tokens_charged"),
           "revisions": (raw.get("metrics") or {}).get("revisions"),
           "stop_code": stop_code(raw.get("trace")), "trace_id": raw.get("trace_id")}
    row["decision_correct"] = raw["status"] in ACCEPTED[item["expected"]]
    row["forbidden"] = "; ".join(forbidden_hits(item, row["answer"])) if raw["status"] == "answered" else ""
    if item["expected"] == "answer" and raw["status"] == "answered":
        det = deterministic_grade(item, row["answer"])
        row.update(key_fact_recall=det["key_fact_recall"], det_verdict=det["verdict"])
        cites = citation_check(item, raw.get("sources") or [])
        row.update(gold_doc_cited=cites["gold_doc_cited"], gold_page_cited=cites["gold_page_cited"],
                   cited_docs=";".join(cites["cited_docs"]))
        judged = judge.grade(item, row["answer"]) if judge else {"verdict": None}
        row["judge_verdict"] = judged.get("verdict")
        row["judge_reason"] = judged.get("reason") or judged.get("error", "")
        row["verdict"] = judged.get("verdict") or det["verdict"]
        row["missing"] = "; ".join(map(str, (judged.get("missing") or det["missing"])[:5]))
        row["wrong"] = "; ".join(map(str, (judged.get("wrong") or [])[:5]))
    else:
        row["verdict"] = None
    if retrieval:
        row["retrieval_evidence_hit"] = retrieval.get("evidence_hit")
    row["failure_category"] = classify(item, row, retrieval) or ""
    row["passed"] = not row["failure_category"]
    row["manual_category"] = ""
    return row


def load_retrieval(path: Path) -> dict:
    if not path or not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        return {r["id"]: r for r in csv.DictReader(fh)}


def push_scores(rows: list[dict]) -> int:
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return 0
    try:
        from langfuse import Langfuse
        lf = Langfuse()
    except Exception as exc:
        print(f"! Langfuse scores skipped: {exc}")
        return 0
    sent = 0
    for r in rows:
        if not r.get("trace_id"):
            continue
        try:
            lf.create_score(name="decision_correct", value=1 if r["decision_correct"] else 0,
                            trace_id=r["trace_id"], data_type="BOOLEAN")
            lf.create_score(name="passed", value=1 if r["passed"] else 0, trace_id=r["trace_id"], data_type="BOOLEAN",
                            comment=r.get("judge_reason") or None)
            if r.get("verdict"):
                lf.create_score(name="answer_verdict", value=r["verdict"], trace_id=r["trace_id"], data_type="CATEGORICAL")
            if r["failure_category"]:
                lf.create_score(name="failure_category", value=r["failure_category"], trace_id=r["trace_id"],
                                data_type="CATEGORICAL")
            sent += 1
        except Exception as exc:
            print(f"! Score for {r['id']} not sent: {exc}")
    lf.flush()
    return sent


# ------------------------------------------------------------------------ reporting

def pct(values) -> str:
    values = [v for v in values if v is not None]
    return f"{100 * sum(bool(v) for v in values) / len(values):.0f}%" if values else "n/a"


def summarise(rows: list[dict], label: str, judged: bool, mode: str) -> str:
    lines = [f"# End-to-end evaluation ({label})", "",
             f"Run {datetime.now():%Y-%m-%d %H:%M}, agent mode `{mode}`, {len(rows)} questions, each in a fresh "
             f"conversation. Answer correctness: {'LLM judge (same Qwen model) with deterministic fallback' if judged else 'deterministic key-fact and number checks'}.",
             "", "## Overview", "",
             "| Set | Questions | Passed | Decision correct | Answer correct* | Partially correct* | Gold doc cited* | Gold page cited* |",
             "|---|---|---|---|---|---|---|---|"]
    for name, group in [("All", rows)] + sorted(((s, [r for r in rows if r["set"] == s]) for s in {r["set"] for r in rows})):
        answerable = [r for r in group if r["expected"] == "answer"]
        lines.append(f"| {name} | {len(group)} | {pct(r['passed'] for r in group)} | {pct(r['decision_correct'] for r in group)} | "
                     f"{pct(r.get('verdict') == 'correct' for r in answerable)} | "
                     f"{pct(r.get('verdict') == 'partially_correct' for r in answerable)} | "
                     f"{pct(r.get('gold_doc_cited') for r in answerable)} | "
                     f"{pct(r.get('gold_page_cited') for r in answerable if r.get('gold_page_cited') is not None)} |")
    lines += ["", "\\* Over questions expecting an answer. Answer correct and partially correct count an unanswered "
              "question as not correct; gold document and page cited are over the questions that were answered.", "",
              "## Behaviour by expected decision", "",
              "| Expected | n | answered | refused | clarify | abstained | error | correct decision |", "|---|---|---|---|---|---|---|---|"]
    for exp in ("answer", "refuse", "clarify", "abstain"):
        group = [r for r in rows if r["expected"] == exp]
        if group:
            c = Counter(r["status"] for r in group)
            lines.append(f"| {exp} | {len(group)} | " + " | ".join(str(c.get(s, 0)) for s in
                         ("answered", "refused", "clarify", "abstained", "error")) + f" | {pct(r['decision_correct'] for r in group)} |")
    lines += ["", "## By category", "", "| Set | Category | n | Passed | Answer correct | Abstained |", "|---|---|---|---|---|---|"]
    groups = defaultdict(list)
    for r in rows:
        groups[(r["set"], r["category"])].append(r)
    for (s, c), group in sorted(groups.items()):
        ans = [r for r in group if r["expected"] == "answer"]
        lines.append(f"| {s} | {c} | {len(group)} | {pct(r['passed'] for r in group)} | "
                     f"{pct(r.get('verdict') == 'correct' for r in ans) if ans else 'n/a'} | {pct(r['status'] == 'abstained' for r in group)} |")
    failures = Counter(r["failure_category"] for r in rows if r["failure_category"])
    lines += ["", "## Failure taxonomy (automatic first pass)", "", "| Category | Count | Meaning |", "|---|---|---|"]
    for cat, n in failures.most_common():
        lines.append(f"| {cat} | {n} | {TAXONOMY[cat]} |")
    stops = Counter(r["stop_code"] for r in rows if r["status"] == "abstained" and r["stop_code"])
    if stops:
        lines += ["", "Stop codes behind abstentions: " + ", ".join(f"{k} ({v})" for k, v in stops.most_common()) + "."]
    secs = [r["seconds"] for r in rows if r.get("seconds")]
    toks = [r["tokens"] for r in rows if r.get("tokens")]
    if secs:
        q = statistics.quantiles(secs, n=10) if len(secs) >= 10 else [max(secs)] * 9
        lines += ["", "## Cost and latency", "",
                  f"Latency per question: median {statistics.median(secs):.1f} s, 90th percentile {q[8]:.1f} s. "
                  f"Tokens per question: mean {statistics.mean(toks):,.0f}" + (f", total {sum(toks):,}." if toks else ".") +
                  f" Questions needing a revision: {sum(1 for r in rows if (r.get('revisions') or 0) > 0)}."]
    lines += ["", "Review `graded.csv`: confirm or correct each automatic category in `manual_category`; "
                  "subtler failures such as scope conflation need a human reading."]
    return "\n".join(lines) + "\n"


def failures_report(rows: list[dict], trace_url) -> str:
    lines = ["# Failures", ""]
    for r in rows:
        if r["passed"]:
            continue
        link = trace_url(r.get("trace_id"))
        lines += [f"## {r['id']} ({r['set']}, {r['category']}): {r['failure_category']}", "",
                  f"**Question:** {r['question']}", "", f"**Expected:** {r['expected']}. **Got:** {r['status']}"
                  + (f" (stop code {r['stop_code']})" if r.get("stop_code") else ""), "",
                  f"**Reference:** {r['reference']}", "", f"**Answer:** {r['answer'].strip()}", ""]
        for key, name in (("judge_reason", "Judge"), ("missing", "Missing"), ("wrong", "Wrong"), ("forbidden", "Forbidden content"),
                          ("cited_docs", "Cited documents")):
            if r.get(key):
                lines += [f"**{name}:** {r[key]}", ""]
        lines += [f"**Trace:** {link or r.get('trace_id') or 'none'}", ""]
    return "\n".join(lines)


def trace_url_builder():
    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        return lambda trace_id: None
    try:
        from langfuse import Langfuse
        projects = Langfuse().api.projects.get()
        project = projects.data[0].id if projects.data else None
    except Exception:
        return lambda trace_id: None
    base = (os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "https://cloud.langfuse.com").rstrip("/")
    return lambda trace_id: f"{base}/project/{project}/traces/{trace_id}" if (project and trace_id) else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--label", default=datetime.now().strftime("e2e-%Y%m%d-%H%M"))
    parser.add_argument("--agent-url", default="http://127.0.0.1:8000")
    parser.add_argument("--sets", default="rbi,tax")
    parser.add_argument("--ids", help="comma-separated question IDs")
    parser.add_argument("--limit", type=int, help="run only the first N questions")
    parser.add_argument("--no-judge", action="store_true", help="grade with deterministic checks only")
    parser.add_argument("--fresh", action="store_true", help="discard earlier results for this label")
    parser.add_argument("--regrade", action="store_true", help="re-grade saved answers without calling the agent")
    parser.add_argument("--retrieval-results", default=str(DEFAULT_RETRIEVAL))
    parser.add_argument("--no-scores", action="store_true", help="do not attach scores to Langfuse traces")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(ROOT / ".env")

    items = load_gold({s.strip().lower() for s in args.sets.split(",")})
    if args.ids:
        wanted = {i.strip() for i in args.ids.split(",")}
        items = [i for i in items if i["id"] in wanted]
    if args.limit:
        items = items[: args.limit]
    out = ROOT / "eval_results" / args.label
    out.mkdir(parents=True, exist_ok=True)
    raw_path = out / "results.jsonl"
    if args.fresh and raw_path.exists():
        raw_path.unlink()
    saved = {}
    if raw_path.exists():
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            saved[record["id"]] = record

    mode = "unknown"
    if not args.regrade:
        headers = {"X-API-Key": os.environ["AGENT_API_KEY"]} if os.getenv("AGENT_API_KEY") else {}
        http = httpx.Client(base_url=args.agent_url.rstrip("/"), headers=headers, timeout=240, trust_env=False)
        try:
            mode = http.get("/health").json().get("mode", "unknown")
        except httpx.HTTPError as exc:
            sys.exit(f"The agent is not reachable at {args.agent_url} ({exc}). Start it first.")
        if mode != "qwen":
            print("! The agent is in demo mode: its answers only repeat passages, so the grades are not meaningful.")
        todo = [i for i in items if i["id"] not in saved]
        print(f"{len(items)} questions, {len(items) - len(todo)} already done, {len(todo)} to run. Results: {out}")
        with raw_path.open("a", encoding="utf-8") as fh:
            for n, item in enumerate(todo, 1):
                print(f"[{n}/{len(todo)}] {item['id']:<8} …", end=" ", flush=True)
                record = run_question(http, item, args.label)
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                saved[item["id"]] = record
                ok = record["status"] in ACCEPTED[item["expected"]]
                print(f"{record['status']:<10} {record.get('seconds', 0):>5.0f}s  expected {item['expected']}"
                      + ("" if ok else "  ✗") + (f"  ({record['error'][:80]})" if record.get("error") else ""))
        http.close()

    judge = None
    if not args.no_judge:
        try:
            judge = Judge()
            print("Grading with the LLM judge…")
        except Exception as exc:
            print(f"! LLM judge unavailable ({exc}); using deterministic grading.")
    retrieval = load_retrieval(Path(args.retrieval_results)) if args.retrieval_results else {}
    rows = [grade(item, saved[item["id"]], judge, retrieval.get(item["id"])) for item in items if item["id"] in saved]
    if not rows:
        sys.exit("No results to grade.")

    fields = ["set", "id", "category", "expected", "status", "decision_correct", "verdict", "judge_verdict",
              "key_fact_recall", "gold_doc_cited", "gold_page_cited", "forbidden", "failure_category",
              "manual_category", "passed", "stop_code", "revisions", "tokens", "seconds", "retrieval_evidence_hit",
              "missing", "wrong", "judge_reason", "cited_docs", "trace_id", "question", "reference", "answer"]
    with (out / "graded.csv").open("w", newline="", encoding="utf-8-sig") as fh:  # utf-8-sig opens cleanly in Excel
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    summary = summarise(rows, args.label, judge is not None, mode)
    (out / "summary.md").write_text(summary, encoding="utf-8")
    (out / "failures.md").write_text(failures_report(rows, trace_url_builder()), encoding="utf-8")
    sent = 0 if args.no_scores else push_scores(rows)
    print("\n" + summary)
    print(f"Wrote {out / 'summary.md'}, failures.md and graded.csv"
          + (f"; scores attached to {sent} Langfuse traces." if sent else "."))


if __name__ == "__main__":
    main()
