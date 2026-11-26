"""Retrieval-only evaluation against the RBI and tax gold sets.

Measures whether the right documents, pages and evidence text reach the agent,
independently of the language model. Uses the question text as the query
(the agent's planner may rewrite it, usually for the better).

  python scripts/eval_retrieval.py                       # in-process, uses RAG_* env vars
  python scripts/eval_retrieval.py --base-url http://127.0.0.1:8001   # against a running service
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from regassist_rag.catalog import Catalog  # noqa: E402
from regassist_rag.lexical import tokenize  # noqa: E402

EVIDENCE_THRESHOLD = 0.6


# ------------------------------------------------------------------ gold sets

def _pages(text: str) -> set[int]:
    pages = set()
    for match in re.finditer(r"pages?\s+(\d+)(?:\s*[–-]\s*(\d+))?", text):
        start = int(match.group(1))
        end = int(match.group(2) or start)
        pages.update(range(start, end + 1))
    return pages


def load_rbi_gold(data_dir: Path, catalog: Catalog) -> list[dict]:
    items = json.loads((data_dir / "RBI/gold/rbi_eval_30.json").read_text(encoding="utf-8"))
    out = []
    for item in items:
        targets = []
        for segment in item["Refer"].split(";"):
            file_match = re.search(r"([\w\-().]+\.pdf)", segment)
            if not file_match:
                continue
            doc_id = catalog.by_filename.get(file_match.group(1))
            targets.append({"document_id": doc_id, "filename": file_match.group(1), "pages": _pages(segment)})
        out.append({
            "set": "RBI", "id": item["id"], "category": item["type"], "authority": "RBI",
            "question": item["Question"], "targets": targets,
            "evidence": [item.get("Golden Para") or ""],
            "answerable": item["type"] != "unanswerable" and bool(targets),
        })
    return out


def load_tax_gold(data_dir: Path, catalog: Catalog) -> list[dict]:
    items = json.loads((data_dir / "Tax/gold/gold_eval_set.json").read_text(encoding="utf-8"))
    out = []
    for item in items:
        targets = []
        if item.get("source_doc"):
            files = [f.strip() for f in item["source_doc"].split("|")]
            for f in dict.fromkeys(files):
                targets.append({"document_id": catalog.by_filename.get(f), "filename": f,
                                "pages": set(item.get("source_pages") or [])})
        out.append({
            "set": "TAX", "id": item["id"], "category": item["category"], "authority": "TAX",
            "question": item["question"], "targets": targets,
            "evidence": [e for e in item.get("evidence") or [] if e],
            "answerable": item.get("expected_decision") == "answer" and bool(targets),
        })
    return out


# ------------------------------------------------------------------ clients

class LocalClient:
    def __init__(self):
        from regassist_rag.config import Settings
        from regassist_rag.index import Retriever
        self.r = Retriever.load_or_build(Settings.from_env())
        self.snapshot = self.r.snapshot

    def search(self, query, authority, limit):
        return self.r.search_documents(query, authority, limit)["hits"]

    def fetch(self, ids, query):
        return self.r.fetch_passages(ids, query)["passages"]


class HttpClient:
    def __init__(self, base_url, api_key=""):
        import httpx
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=60,
                                 headers={"X-API-Key": api_key} if api_key else {})
        self.snapshot = self.http.get("/health").json().get("snapshot")

    def search(self, query, authority, limit):
        r = self.http.post("/v1/tools/search_documents", json={"query": query, "authority": authority, "limit": limit})
        r.raise_for_status()
        return r.json()["hits"]

    def fetch(self, ids, query):
        r = self.http.post("/v1/tools/fetch_passages", json={"document_ids": ids, "query": query})
        r.raise_for_status()
        return r.json()["passages"]


# ------------------------------------------------------------------ metrics

def token_recall(needle: str, haystack: str) -> float:
    need = tokenize(needle)
    if not need:
        return 0.0
    have = set(tokenize(haystack))
    return sum(1 for t in need if t in have) / len(need)


def evaluate(client, gold: list[dict]) -> list[dict]:
    rows = []
    for q in gold:
        if not q["answerable"]:
            continue
        gold_ids = [t["document_id"] for t in q["targets"]]
        in_corpus = [g for g in gold_ids if g]
        row = {"set": q["set"], "id": q["id"], "category": q["category"], "question": q["question"],
               "gold_docs": ";".join(t["filename"] for t in q["targets"])}
        if not in_corpus:
            row.update(status="gold_doc_not_in_corpus")
            rows.append(row)
            continue
        started = time.time()
        hits = client.search(q["question"], q["authority"], 5)
        hit_ids = [h["document_id"] for h in hits]
        rank = next((i + 1 for i, d in enumerate(hit_ids) if d in in_corpus), None)
        top3 = hit_ids[:3]
        passages = client.fetch(top3, q["question"]) if top3 else []
        oracle = client.fetch(list(dict.fromkeys(in_corpus))[:5], q["question"])
        latency = time.time() - started

        gold_pages = {(t["document_id"], p) for t in q["targets"] if t["document_id"] for p in t["pages"]}

        def page_hit(ps, slack):
            return any((p["document_id"], p["page"] + d) in gold_pages for p in ps for d in range(-slack, slack + 1))

        def evidence(ps):
            if not q["evidence"] or not ps:
                return 0.0
            joined = "\n".join(p["text"] for p in ps)
            return min(token_recall(e, joined) for e in q["evidence"])

        row.update(
            status="evaluated" if len(in_corpus) == len(gold_ids) else "partial_gold_in_corpus",
            hit_at_1=int(rank == 1), hit_at_3=int(rank is not None and rank <= 3), hit_at_5=int(rank is not None),
            all_gold_at_5=int(all(g in hit_ids for g in in_corpus)),
            reciprocal_rank=round(1 / rank, 4) if rank else 0.0,
            page_hit=int(page_hit(passages, 0)) if gold_pages else "",
            page_hit_pm1=int(page_hit(passages, 1)) if gold_pages else "",
            evidence_recall=round(evidence(passages), 3),
            evidence_hit=int(evidence(passages) >= EVIDENCE_THRESHOLD),
            oracle_evidence_hit=int(evidence(oracle) >= EVIDENCE_THRESHOLD),
            top_hits=";".join(hit_ids), latency_s=round(latency, 3),
        )
        rows.append(row)
    return rows


def summarise(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        if row.get("status") in ("evaluated", "partial_gold_in_corpus"):
            groups[(row["set"], "ALL")].append(row)
            groups[(row["set"], row["category"])].append(row)
    table = []
    for (gset, category), items in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1] != "ALL", kv[0][1])):
        def mean(key):
            values = [r[key] for r in items if r.get(key) != ""]
            return f"{100 * sum(values) / len(values):.0f}%" if values else "n/a"
        table.append({"set": gset, "category": category, "n": len(items),
                      "hit@1": mean("hit_at_1"), "hit@3": mean("hit_at_3"), "hit@5": mean("hit_at_5"),
                      "all_gold@5": mean("all_gold_at_5"),
                      "MRR": f"{sum(r['reciprocal_rank'] for r in items) / len(items):.2f}",
                      "page_hit": mean("page_hit"), "page±1": mean("page_hit_pm1"),
                      "evidence_hit": mean("evidence_hit"), "oracle_evidence": mean("oracle_evidence_hit")})
    return table


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--data-dir", default=str(ROOT.parent / "data"))
    parser.add_argument("--out", default=str(ROOT / "eval_results"))
    parser.add_argument("--label", default="")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    catalog = Catalog.load(data_dir)
    gold = load_rbi_gold(data_dir, catalog) + load_tax_gold(data_dir, catalog)
    client = HttpClient(args.base_url, args.api_key) if args.base_url else LocalClient()
    rows = evaluate(client, gold)
    table = summarise(rows)

    label = args.label or time.strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / label
    out.mkdir(parents=True, exist_ok=True)
    fields = sorted({k for r in rows for k in r}, key=lambda k: (k not in ("set", "id", "category"), k))
    with (out / "per_question.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    missing = [r for r in rows if r.get("status") == "gold_doc_not_in_corpus"]
    skipped = sum(1 for q in gold if not q["answerable"])
    lines = [f"# Retrieval evaluation ({label})", "",
             f"Index snapshot: `{client.snapshot}`. Query = gold question text. "
             f"Evidence hit = at least {int(EVIDENCE_THRESHOLD * 100)}% of the gold evidence tokens appear in the "
             "fetched passages for the top-3 searched documents; oracle evidence uses the gold documents directly.",
             "", f"Skipped {skipped} refusal/unanswerable items; {len(missing)} answerable items cite documents "
             "that are not in the corpus.", ""]
    header = list(table[0].keys()) if table else []
    lines += ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(r[h]) for h in header) + " |" for r in table]
    if missing:
        by_doc = defaultdict(int)
        for r in missing:
            by_doc[r["gold_docs"]] += 1
        lines += ["", "## Gold questions whose source documents are missing from the corpus", ""]
        lines += [f"- {doc}: {n}" for doc, n in sorted(by_doc.items(), key=lambda kv: -kv[1])]
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
