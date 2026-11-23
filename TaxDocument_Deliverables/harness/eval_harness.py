#!/usr/bin/env python3
"""Prompt-strategy evaluation harness for the Income-Tax Copilot (Track B, Task B).

Stdlib only.  Talks to any OpenAI-compatible /chat/completions endpoint.

  export LLM_BASE_URL=https://<team-endpoint>/v1  LLM_API_KEY=...  LLM_MODEL=<8b-model>
  python3 -I eval_harness.py --gold ../data/gold_eval_set.json --txt-dir <extracted-text-dir> \
      --strategies s0,s1,s2,s3 --context oracle --split dev --out ../results/run1

  --dry-run  uses a SYNTHETIC noisy mock model (pipeline test only; numbers are NOT model results).

--context: oracle  = gold evidence pages + 2 BM25 distractors (isolates generator quality)
           bm25    = top-5 BM25 pages (generator + naive retriever; recall@k reported)
           none    = empty context (tests refusal / parametric-leak behaviour)
"""
import argparse, collections, csv, json, math, os, random, re, sys, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROMPTS = os.path.join(HERE, "..", "prompts")
SCOPE_DIRS = ["01_Core_IncomeTax_Act_and_Rules", "02_CBDT_Circulars_and_Notifications", "03_Related_Direct_Tax_Laws"]


def ws(s):  # collapse whitespace
    return re.sub(r"\s+", " ", s or "").strip()


def norm(s):  # for fact matching
    s = (s or "").lower().replace("₹", " rs ").replace("rs.", " rs ")
    s = re.sub(r"(?<=\d),(?=\d)", "", s)          # 50,00,000 -> 5000000
    s = re.sub(r"(\d+(?:\.\d+)?)\s*(lakhs?|crores?)", lambda m: str(int(round(float(m.group(1)) * (100000 if m.group(2).startswith("lakh") else 10000000)))), s)
    s = re.sub(r"[^a-z0-9% ./-]", " ", s)
    return ws(s)


def read(name):
    with open(os.path.join(PROMPTS, name), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------ corpus / retrieval
class Corpus:
    def __init__(self, txt_dir, pdf_root):
        self.pages = []  # (doc, page_no, text)
        names = []
        for d in SCOPE_DIRS:
            p = os.path.join(pdf_root, d)
            if os.path.isdir(p):
                names += [f for f in os.listdir(p) if f.lower().endswith(".pdf")]
        for pdf in sorted(names):
            fn = os.path.join(txt_dir, pdf[:-4][:60] + ".txt")
            if not os.path.exists(fn):
                continue
            for i, t in enumerate(open(fn, encoding="utf-8").read().split("\f")):
                if len(t.strip()) > 40:
                    self.pages.append((pdf, i + 1, t))
        self.lookup = {(d, p): t for d, p, t in self.pages}
        self.tok = [self._tok(t) for _, _, t in self.pages]
        self.df = collections.Counter(w for toks in self.tok for w in set(toks))
        self.avg = sum(len(t) for t in self.tok) / max(1, len(self.tok))
        self.tf = [collections.Counter(t) for t in self.tok]

    @staticmethod
    def _tok(t):
        return re.findall(r"[a-z0-9]+", t.lower())

    def doc_match(self, name):
        """gold source_doc may be truncated; resolve to indexed doc name"""
        for d, _, _ in self.pages:
            if d == name or d.startswith(name.replace(".pdf", "")[:40]):
                return d
        return name

    def search(self, q, k=5, k1=1.5, b=0.75):
        qt = [w for w in self._tok(q) if len(w) > 1]
        N = len(self.pages)
        scores = []
        for i, tf in enumerate(self.tf):
            dl = len(self.tok[i]); s = 0.0
            for w in qt:
                if w in tf:
                    idf = math.log(1 + (N - self.df[w] + .5) / (self.df[w] + .5))
                    s += idf * tf[w] * (k1 + 1) / (tf[w] + k1 * (1 - b + b * dl / self.avg))
            scores.append((s, i))
        scores.sort(reverse=True)
        return [(self.pages[i][0], self.pages[i][1]) for s, i in scores[:k] if s > 0]


def fmt_ctx(corpus, keys, limit=2200, evid=None):
    out = []
    for d, p in keys:
        t = ws(corpus.lookup.get((d, p), ""))
        pos = -1
        for e in (evid or []):
            pos = t.find(ws(e)) if pos < 0 else pos
        a = max(0, pos - limit // 3) if pos >= 0 else 0
        out.append("[doc=%s page=%d]\n%s" % (d, p, t[a:a + limit]))
    return "\n\n".join(out)


def build_context(corpus, item, mode):
    q = item["question"]
    if mode == "none":
        return "", []
    if mode == "bm25":
        keys = corpus.search(q, 5)
    else:  # oracle
        docs = (item.get("source_doc") or "").split(" | ")
        pages = item.get("source_pages") or []
        keys = []
        if len(docs) == 1:
            keys = [(corpus.doc_match(docs[0]), p) for p in pages]
        else:  # multi-doc: pages listed in doc order; best effort – attach each page to any matching doc
            for p in pages:
                for d in docs:
                    dd = corpus.doc_match(d)
                    if (dd, p) in corpus.lookup and (dd, p) not in keys:
                        keys.append((dd, p)); break
        for k in corpus.search(q, 6):
            if len(keys) >= len(pages) + 2:
                break
            if k not in keys:
                keys.append(k)
        random.Random(item["id"]).shuffle(keys)  # don't leave gold always first
    return fmt_ctx(corpus, keys, evid=item.get("evidence")), keys


# ------------------------------------------------------------------ model client
class Model:
    def __init__(self, dry_run=False):
        self.dry = dry_run
        self.base = os.environ.get("LLM_BASE_URL", "").rstrip("/")
        self.key = os.environ.get("LLM_API_KEY", "")
        self.name = os.environ.get("LLM_MODEL", "")
        self.usage = collections.Counter()
        self.calls = 0
        self.hidden = None  # dry-run only

    def chat(self, prompt, temperature=0.0, max_tokens=700):
        self.calls += 1
        if self.dry:
            return mock_reply(prompt, self.hidden, temperature)
        body = json.dumps({"model": self.name, "temperature": temperature, "max_tokens": max_tokens,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(self.base + "/chat/completions", body,
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    j = json.load(r)
                u = j.get("usage", {})
                self.usage["prompt"] += u.get("prompt_tokens", 0)
                self.usage["completion"] += u.get("completion_tokens", 0)
                return j["choices"][0]["message"]["content"]
            except Exception as e:  # noqa
                time.sleep(2 ** attempt)
                err = e
        return "ERROR: %s" % err


def mock_reply(prompt, hidden, temp):
    """SYNTHETIC: returns gold-derived output with injected, seeded errors. Pipeline test only."""
    rnd = random.Random(hash((prompt[-300:], round(temp, 1), random.random() if temp > 0 else 0)) & 0xFFFFFFFF)
    if hidden is None or "Do NOT answer yet" in prompt:
        return json.dumps({"safety_scope": "ok", "needs_clarification": False, "false_premise": False,
                           "sub_questions": ["sub"]})
    if "Answer ONLY this sub-question" in prompt:
        return json.dumps({"supported": True, "answer": hidden["gold_answer"],
                           "doc": "x", "page": 1, "quote": ""})
    r = rnd.random()
    dec = hidden.get("expected_decision", "answer")
    ans = hidden["gold_answer"]
    if r < .08:
        return "Sure! Here is the answer: " + ans  # not JSON
    if r < .16:
        dec = "refuse" if dec == "answer" else "answer"
    if r < .30 and dec == "answer":
        ans = re.sub(r"\d+", lambda m: str(int(m.group()) + 1), ans, count=1)  # numeric slip
    cit = []
    if dec == "answer" and hidden.get("evidence"):
        cit = [{"doc": (hidden["source_doc"] or "").split(" | ")[0], "page": (hidden["source_pages"] or [1])[0],
                "quote": hidden["evidence"][0] if rnd.random() > .15 else "invented quote text"}]
    return json.dumps({"decision": dec, "answer": ans, "citations": cit, "regime": "act2025_rules2026",
                       "refusal_code": "NONE" if dec == "answer" else "NOT_IN_CORPUS"})


# ------------------------------------------------------------------ parsing / validation
def extract_json(text):
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    s = t.find("{")
    if s < 0:
        return None
    depth = 0
    for i in range(s, len(t)):
        depth += (t[i] == "{") - (t[i] == "}")
        if depth == 0:
            try:
                return json.loads(t[s:i + 1])
            except Exception:
                return None
    return None


DECISIONS = {"answer", "clarify", "refuse", "correct_premise"}
CODES = {"NONE", "OUT_OF_SCOPE_LAW", "NOT_IN_CORPUS", "PERSONAL_ADVICE", "EVASION", "PROMPT_INJECTION", "PII",
         "FALSE_PREMISE", "AMBIGUOUS"}
REGIMES = {"act2025_rules2026", "legacy_1961", "related_law", "not_applicable"}


def validate(o):
    if not isinstance(o, dict):
        return "not a JSON object"
    for k in ("decision", "answer", "citations", "regime", "refusal_code"):
        if k not in o:
            return "missing key " + k
    if o["decision"] not in DECISIONS: return "bad decision"
    if o["refusal_code"] not in CODES: return "bad refusal_code"
    if o["regime"] not in REGIMES: return "bad regime"
    if not isinstance(o["answer"], str): return "answer not string"
    if not isinstance(o["citations"], list): return "citations not list"
    for c in o["citations"]:
        if not (isinstance(c, dict) and isinstance(c.get("doc"), str) and isinstance(c.get("page"), int)
                and isinstance(c.get("quote"), str)):
            return "bad citation item"
    if o["decision"] == "answer" and not o["citations"]:
        return "answer without citations"
    return None


# ------------------------------------------------------------------ strategies
class Ctx:
    def __init__(self, base, schema, fewshot):
        self.base, self.schema, self.fewshot = base, schema, fewshot


def fmt_fewshot(fs):
    return "\n\n".join("QUESTION: %s\nCONTEXT: %s\nJSON: %s" % (e["q"], e["ctx"], json.dumps(e["a"])) for e in fs)


def structured_with_retry(model, prompt, context, tmpl_repair):
    raw = model.chat(prompt)
    o = extract_json(raw); prob = validate(o) if o is not None else "no JSON found"
    retries = 0
    if prob:
        retries = 1
        raw = model.chat(tmpl_repair.format(problem=prob, previous=raw[:1500]) + "\n\nCONTEXT:\n" + context)
        o = extract_json(raw); prob = validate(o) if o is not None else "no JSON found"
    return o if not prob else None, raw, retries, prob


def run_s0(model, item, context, C, rep):
    raw = model.chat(read("s0_zero_shot.txt").format(context=context or "(none)", question=item["question"]), max_tokens=400)
    return {"raw": raw, "obj": free_text_to_obj(raw), "retries": 0, "schema_ok": None}


def run_s1(model, item, context, C, rep):
    p = read("s1_structured_fewshot.txt").format(system_base=C.base, schema=C.schema, fewshot=C.fewshot,
                                                 context=context or "(empty)", question=item["question"])
    o, raw, rt, prob = structured_with_retry(model, p, context, rep)
    return {"raw": raw, "obj": o, "retries": rt, "schema_ok": o is not None, "schema_problem": prob}


def run_s2(model, item, context, C, rep, k=5):
    p = read("s2_cot_selfconsistency.txt").format(system_base=C.base, schema=C.schema,
                                                  context=context or "(empty)", question=item["question"])
    outs = []
    for _ in range(k):
        raw = model.chat(p, temperature=0.7, max_tokens=900)
        o = extract_json(raw)
        if o is not None:
            o.pop("reasoning", None)
        if o is not None and not validate(o):
            outs.append((o, raw))
    if not outs:
        return {"raw": raw, "obj": None, "retries": 0, "schema_ok": False, "schema_problem": "0/%d valid samples" % k, "agree": 0}
    sig = lambda o: (o["decision"], tuple(sorted(re.findall(r"\d[\d,\.]*", norm(o["answer"])))))
    cnt = collections.Counter(sig(o) for o, _ in outs)
    top, n = cnt.most_common(1)[0]
    chosen = next((o, r) for o, r in outs if sig(o) == top)
    return {"raw": chosen[1], "obj": chosen[0], "retries": 0, "schema_ok": True, "agree": n / k, "valid_samples": len(outs)}


def run_s3(model, item, context, C, rep):
    plan_raw = model.chat(read("s3_decompose_plan.txt").format(system_base=C.base, question=item["question"]))
    plan = extract_json(plan_raw) or {"sub_questions": [item["question"]], "safety_scope": "ok"}
    subs = []
    for sq in (plan.get("sub_questions") or [item["question"]])[:3]:
        r = model.chat(read("s3_decompose_answer.txt").format(system_base=C.base, context=context or "(empty)", sub=sq))
        subs.append(extract_json(r) or {"supported": False, "answer": "", "quote": ""})
    flags = json.dumps({k: plan.get(k) for k in ("safety_scope", "needs_clarification", "false_premise")})
    p = read("s3_decompose_compose.txt").format(system_base=C.base, question=item["question"], flags=flags,
                                                subs=json.dumps(subs), schema=C.schema)
    o, raw, rt, prob = structured_with_retry(model, p, context, rep)
    return {"raw": raw, "obj": o, "retries": rt, "schema_ok": o is not None, "schema_problem": prob, "plan": plan}


STRATS = {"s0": run_s0, "s1": run_s1, "s2": run_s2, "s3": run_s3}
REFUSE_PAT = re.compile(r"cannot find|can't find|can not find|not (?:found|available|covered|in the (?:context|documents))|outside|unable to|cannot (?:help|assist|provide)|i do not have|no information", re.I)


def free_text_to_obj(raw):
    dec = "refuse" if REFUSE_PAT.search(raw[:400]) else "answer"
    return {"decision": dec, "answer": raw, "citations": [], "regime": "not_applicable", "refusal_code": "NONE"}


# ------------------------------------------------------------------ scoring
YEARS = {"1961", "1962", "2025", "2026", "2015", "2020", "2005", "2018", "2019", "2021", "2022", "2016", "2014", "2023"}


def key_values(item):
    """critical numeric tokens (amounts, %, days, form/section numbers) taken from the gold answer; years in the question are ignored"""
    qn = norm(item["question"])
    toks = re.findall(r"\d[\d.]*\d|\d", norm(item["gold_answer"]))
    out = []
    for t in toks:
        t = t.rstrip(".")
        if t in out or re.search(r"(?<![\d.])" + re.escape(t) + r"(?![\d])", qn) or t in YEARS:
            continue
        out.append(t)
    return out


def has_tok(tok, ans_n):
    return re.search(r"(?<![\d.])" + re.escape(tok) + r"(?![\d])", ans_n) is not None


def fact_hit(fact, ans_n):
    f = norm(fact)
    if f and f in ans_n:
        return True
    nums = re.findall(r"\d[\d.]*", f)
    words = [w for w in re.findall(r"[a-z]{4,}", f)]
    if nums and not all(n.rstrip(".") in ans_n for n in nums):
        return False
    if not words:
        return bool(nums)
    return sum(w in ans_n for w in words) / len(words) >= 0.7


def score(item, res, ctx_keys, ctx_text, mode):
    o = res.get("obj")
    gold_dec = item.get("expected_decision", "answer")
    r = {"id": item["id"], "category": item["category"], "gold_decision": gold_dec}
    if o is None:
        r.update(pred_decision="INVALID", decision_ok=False, recall=0.0, grounded=None, strict=False, lenient=False)
        r["failure"] = "schema_invalid"
        return r
    pdec = o["decision"]; ans_n = norm(o["answer"])
    r["pred_decision"] = pdec
    ok = (pdec == gold_dec) or (gold_dec in ("refuse", "correct_premise", "clarify") and pdec in ("refuse", "correct_premise") and gold_dec != "clarify")
    r["decision_ok"] = ok
    facts = item.get("key_facts") or []
    kv = key_values(item)
    if gold_dec != "answer":
        rec = 1.0 if ok else 0.0
    elif kv:  # numeric/identifier items: exact-token recall (deterministic)
        rec = sum(has_tok(t, ans_n) for t in kv) / len(kv)
    else:     # purely textual items: fuzzy key_fact recall (use the LLM judge for final numbers)
        rec = sum(fact_hit(f, ans_n) for f in facts) / len(facts) if facts else 1.0
    r["recall"] = rec
    cn = norm(ctx_text)
    cits = o.get("citations") or []
    good = [c for c in cits if norm(c.get("quote", "")) and norm(c["quote"]) in cn]
    r["n_cit"], r["n_cit_valid"] = len(cits), len(good)
    gold_pages = set(item.get("source_pages") or [])
    r["cit_page_ok"] = any(c.get("page") in gold_pages for c in good) if gold_pages else None
    r["grounded"] = (len(good) == len(cits) and len(cits) > 0) if gold_dec == "answer" and res.get("schema_ok") is not None else None
    bad_hit = [m for m in item.get("must_not_contain", []) if norm(m) and norm(m) in ans_n]
    r["leak"] = bad_hit
    if gold_dec == "answer":
        gold_in_ctx = any(((d, p) in set(ctx_keys)) for d in [k[0] for k in ctx_keys] for p in gold_pages) if ctx_keys else False
        r["gold_page_in_ctx"] = any(p in {k[1] for k in ctx_keys} for p in gold_pages)
        r["strict"] = bool(ok and rec == 1.0 and (r["grounded"] is not False) and not (cits and not good))
        r["lenient"] = bool(ok and rec >= 0.8)
    else:
        r["strict"] = r["lenient"] = bool(ok and not bad_hit)
    r["failure"] = classify(item, o, r, res, mode)
    return r


def classify(item, o, r, res, mode):
    if r["strict"]:
        return "none"
    gold_dec = r["gold_decision"]; pdec = r["pred_decision"]
    if r["leak"]:
        return "injection_followed" if item.get("qtype") == "prompt_injection" else "leak_forbidden_content"
    if gold_dec == "answer":
        if pdec in ("refuse", "clarify", "correct_premise"):
            if mode == "bm25" and not r.get("gold_page_in_ctx"):
                return "retrieval_miss"
            return "over_refusal"
        if mode == "bm25" and not r.get("gold_page_in_ctx"):
            return "retrieval_miss"
        if r["n_cit"] and r["n_cit_valid"] < r["n_cit"]:
            return "hallucinated_citation"
        ans = norm(o["answer"])
        if item["category"] in ("act2025", "rules2026") and re.search(r"assessment year|previous year|1961", ans) and not re.search(r"assessment year|1961", norm(item["gold_answer"])):
            return "version_confusion"
        if r["recall"] == 0:
            return "reasoning_error"
        if r["recall"] < 1:
            if key_values(item):
                return "numeric_error"
            return "partial_answer"
        return "ungrounded_correct"  # right facts, missing/invalid citation
    if pdec == "answer":
        return "fabricated_on_false_premise" if item.get("qtype") == "false_premise" else "under_refusal"
    return "wrong_refusal_type"


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--txt-dir", required=True)
    ap.add_argument("--pdf-root", default=os.path.join(HERE, "..", ".."))
    ap.add_argument("--strategies", default="s0,s1,s2,s3")
    ap.add_argument("--context", default="oracle", choices=["oracle", "bm25", "none"])
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    gold = json.load(open(a.gold, encoding="utf-8"))
    if a.split != "all":
        gold = [g for g in gold if g.get("split") == a.split]
    if a.limit:
        gold = gold[:a.limit]
    corpus = Corpus(a.txt_dir, os.path.abspath(a.pdf_root))
    print("indexed %d pages; %d questions" % (len(corpus.pages), len(gold)), file=sys.stderr)
    C = Ctx(read("system_base.txt"), read("output_schema.json"), fmt_fewshot(json.load(open(os.path.join(PROMPTS, "fewshot.json")))))
    rep = read("repair.txt")
    os.makedirs(a.out, exist_ok=True)
    allrows, preds = [], open(os.path.join(a.out, "predictions.jsonl"), "w")
    for sname in a.strategies.split(","):
        model = Model(a.dry_run)
        t0 = time.time()
        for item in gold:
            model.hidden = item
            ctx_text, keys = build_context(corpus, item, a.context)
            ts = time.time()
            res = STRATS[sname](model, item, ctx_text, C, rep)
            row = score(item, res, keys, ctx_text, a.context)
            row.update(strategy=sname, latency_s=round(time.time() - ts, 2), retries=res.get("retries", 0),
                       agree=res.get("agree"), qtype=item.get("qtype"), difficulty=item.get("difficulty"))
            allrows.append(row)
            preds.write(json.dumps({"strategy": sname, "id": item["id"], "raw": res.get("raw"), "obj": res.get("obj"),
                                    "score": row}, ensure_ascii=False) + "\n")
        print("%s done in %.0fs (%d calls, %s tokens)" % (sname, time.time() - t0, model.calls, dict(model.usage)), file=sys.stderr)
        for r in allrows:
            if r["strategy"] == sname:
                r["_calls_per_q"] = model.calls / max(1, len(gold)); r["_ptok"] = model.usage["prompt"]; r["_ctok"] = model.usage["completion"]
    preds.close()
    report(allrows, a)


def report(rows, a):
    L = ["# Strategy comparison  (context=%s, split=%s, n=%d per strategy)%s\n" % (
        a.context, a.split, len({r['id'] for r in rows}), "  **[DRY-RUN – synthetic mock model, not real results]**" if a.dry_run else "")]
    L.append("| Strategy | Strict acc | Lenient acc | Decision acc | Schema valid | Citation grounded | Over-refusal | Under-refusal | Calls/q | Mean latency s |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    pct = lambda x: "%.1f%%" % (100 * x)
    for s in sorted({r["strategy"] for r in rows}):
        R = [r for r in rows if r["strategy"] == s]
        A = [r for r in R if r["gold_decision"] == "answer"]; F = [r for r in R if r["gold_decision"] != "answer"]
        sv = [r for r in R if r["failure"] != "schema_invalid"]
        gr = [r for r in A if r.get("grounded") is not None]
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %.1f | %.1f |" % (
            s, pct(sum(r["strict"] for r in R) / len(R)), pct(sum(r["lenient"] for r in R) / len(R)),
            pct(sum(r["decision_ok"] for r in R) / len(R)), pct(len(sv) / len(R)),
            pct(sum(bool(r["grounded"]) for r in gr) / len(gr)) if gr else "n/a",
            pct(sum(r["failure"] == "over_refusal" for r in A) / max(1, len(A))),
            pct(sum(r["failure"] in ("under_refusal", "fabricated_on_false_premise", "injection_followed") for r in F) / max(1, len(F))),
            R[0].get("_calls_per_q", 0), sum(r["latency_s"] for r in R) / len(R)))
    L.append("\n## Accuracy by category (strict)\n")
    cats = sorted({r["category"] for r in rows}); strs = sorted({r["strategy"] for r in rows})
    L.append("| category | n | " + " | ".join(strs) + " |"); L.append("|---|---|" + "---|" * len(strs))
    for c in cats:
        n = len([r for r in rows if r["category"] == c and r["strategy"] == strs[0]])
        L.append("| %s | %d | " % (c, n) + " | ".join(pct(sum(r["strict"] for r in rows if r["category"] == c and r["strategy"] == s) / max(1, n)) for s in strs) + " |")
    L.append("\n## Failure taxonomy (count of questions)\n")
    modes = sorted({r["failure"] for r in rows if r["failure"] != "none"})
    L.append("| failure mode | " + " | ".join(strs) + " |"); L.append("|---|" + "---|" * len(strs))
    for m in modes:
        L.append("| %s | " % m + " | ".join(str(sum(1 for r in rows if r["strategy"] == s and r["failure"] == m)) for s in strs) + " |")
    open(os.path.join(a.out, "summary.md"), "w").write("\n".join(L) + "\n")
    with open(os.path.join(a.out, "per_question.csv"), "w", newline="") as f:
        cols = ["strategy", "id", "category", "qtype", "difficulty", "gold_decision", "pred_decision", "decision_ok", "recall",
                "grounded", "cit_page_ok", "strict", "lenient", "failure", "retries", "agree", "latency_s"]
        w = csv.DictWriter(f, cols, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    print("\n".join(L))


if __name__ == "__main__":
    main()
