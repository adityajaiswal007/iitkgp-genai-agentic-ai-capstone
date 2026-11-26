"""Index building and hybrid retrieval.

Pipeline per query:
  BM25 over chunks (title + heading + text)  ─┐
  dense cosine over chunks (if configured)   ─┼─ reciprocal rank fusion ─> optional rerank
                                              ┘
Documents are ranked by their best chunks plus a title match signal, which is
what the agent's search_documents tool expects (document-level hits).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx
import numpy as np

from .catalog import Catalog, Document
from .chunking import CHUNKER_VERSION, Chunk, chunk_document
from .config import Settings
from .extract import extract_pages
from .lexical import BM25, tokenize
from .remote import EmbeddingClient, EmbeddingError, RerankClient

log = logging.getLogger(__name__)

RRF_K = 60
LEXICAL_CANDIDATES = 200
DENSE_CANDIDATES = 200
PASSAGE_TEXT_LIMIT = 6000  # agent contract


def index_text(chunk: Chunk, doc: Document) -> str:
    return f"{doc.title}\n{chunk.heading}\n{chunk.subheading}\n{chunk.section}\n{chunk.text}"


def _snapshot_id(docs: list[Document], data_dir: Path, settings: Settings) -> str:
    digest = hashlib.sha256()
    digest.update(f"chunker={CHUNKER_VERSION};{settings.chunk_target_chars};{settings.chunk_max_chars};{settings.chunk_overlap_chars}".encode())
    for doc in sorted(docs, key=lambda d: d.document_id):
        digest.update(doc.document_id.encode())
        digest.update((doc.sha256 or hashlib.sha256((data_dir / doc.path).read_bytes()).hexdigest()).encode())
    return "regassist-" + digest.hexdigest()[:12]


# ---------------------------------------------------------------- building

def build_index(settings: Settings, only_filenames: set[str] | None = None, force: bool = False) -> dict:
    """Extract, chunk and (optionally) embed the corpus into settings.index_dir."""
    catalog = Catalog.load(settings.data_dir)
    docs = catalog.indexable(settings.include_out_of_scope)
    if only_filenames is not None:
        docs = [d for d in docs if d.filename in only_filenames]
    snapshot = _snapshot_id(docs, settings.data_dir, settings)
    out = settings.index_dir
    out.mkdir(parents=True, exist_ok=True)
    info_path = out / "build_info.json"
    if not force and info_path.exists():
        info = json.loads(info_path.read_text(encoding="utf-8"))
        if info.get("snapshot") == snapshot:
            log.info("Index %s is current", snapshot)
            if settings.dense_enabled:
                _try_embeddings(settings)
            return info

    started = time.time()
    chunks: list[Chunk] = []
    empty_pages = 0
    for doc in docs:
        pages = extract_pages(settings.data_dir / doc.path, ocr=settings.ocr)
        empty_pages += sum(1 for p in pages if len(p.strip()) < 30)
        unit = {"act": "section", "rules": "rule"}.get(doc.doc_type, "para")
        chunks.extend(chunk_document(doc.document_id, doc.authority, pages,
                                     settings.chunk_target_chars, settings.chunk_max_chars, unit,
                                     settings.chunk_overlap_chars,
                                     subheadings=doc.authority == "RBI"))  # RBI uses A./B.1 sub-sections
        log.info("Indexed %s (%d pages)", doc.document_id, len(pages))

    with (out / "chunks.jsonl").open("w", encoding="utf-8") as fh:
        for chunk in chunks:
            fh.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")
    info = {
        "snapshot": snapshot,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seconds": round(time.time() - started, 1),
        "documents": len(docs),
        "documents_by_authority": {a: sum(1 for d in docs if d.authority == a) for a in ("RBI", "TAX")},
        "chunks": len(chunks),
        "empty_pages": empty_pages,
        "include_out_of_scope": settings.include_out_of_scope,
        "document_ids": sorted(d.document_id for d in docs),
    }
    info_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
    for stale in out.glob("embeddings*"):
        stale.unlink()
    if settings.dense_enabled:
        _try_embeddings(settings)
    return info


def _try_embeddings(settings: Settings) -> None:
    """Embedding failures leave the service running on keyword search; the next start resumes."""
    try:
        _ensure_embeddings(settings)
    except Exception as exc:
        log.error("Could not finish embedding the corpus (%s). Starting with keyword search only; "
                  "restart later to resume embedding.", exc)


def _embedding_path(settings: Settings) -> Path:
    slug = hashlib.sha1(f"{settings.embedding_model}|{settings.embedding_document_prefix}".encode()).hexdigest()[:10]
    return settings.index_dir / f"embeddings-{slug}.npy"


def _load_chunks(index_dir: Path) -> list[Chunk]:
    with (index_dir / "chunks.jsonl").open(encoding="utf-8") as fh:
        return [Chunk(**json.loads(line)) for line in fh]


def embedding_text(chunk: Chunk, doc: Document) -> str:
    """What the embedding model sees: a short context line, then the chunk.
    Kept short because small embedding models read at most 512 tokens."""
    context = " | ".join(part for part in (doc.title[:160], chunk.subheading, chunk.section) if part)
    return f"{context}\n{chunk.text}"


def embedding_client(settings: Settings, timeout: float) -> EmbeddingClient:
    return EmbeddingClient(settings.embedding_base_url, settings.embedding_model, settings.embedding_api_key,
                           settings.embedding_ca_cert, settings.embedding_batch_size, timeout=timeout,
                           max_tokens=settings.embedding_max_tokens)


def _ensure_embeddings(settings: Settings) -> Path:
    """Embed every chunk once. Each batch is appended to a progress file as soon as it
    returns, so an interrupted run (server at capacity, network drop, Ctrl+C) resumes
    where it stopped and loses at most one request."""
    path = _embedding_path(settings)
    if path.exists():
        return path
    partial = path.with_name(path.stem + ".partial.f32")
    meta = path.with_name(path.stem + ".partial.json")
    catalog = Catalog.load(settings.data_dir)
    chunks = _load_chunks(settings.index_dir)
    texts = [settings.embedding_document_prefix + embedding_text(c, catalog.get(c.document_id)) for c in chunks]

    dimension = json.loads(meta.read_text(encoding="utf-8"))["dimension"] if meta.exists() and partial.exists() else None
    done = partial.stat().st_size // (4 * dimension) if dimension else 0
    if dimension and partial.stat().st_size != done * 4 * dimension:  # torn last write
        with partial.open("r+b") as fh:
            fh.truncate(done * 4 * dimension)
    if done > len(texts):
        partial.unlink()
        meta.unlink()
        dimension, done = None, 0
    if done:
        log.info("Resuming embedding at chunk %d of %d", done, len(texts))
    else:
        log.info("Embedding %d chunks with %s (one-time; progress is saved)", len(texts), settings.embedding_model)

    client = embedding_client(settings, timeout=120)
    started, last_report = time.time(), 0.0
    try:
        with partial.open("ab") as out:
            for start in range(done, len(texts), settings.embedding_batch_size):
                vectors = client.embed(texts[start:start + settings.embedding_batch_size]).astype(np.float32)
                if dimension is None:
                    dimension = vectors.shape[1]
                    meta.write_text(json.dumps({"dimension": dimension, "model": settings.embedding_model}), encoding="utf-8")
                out.write(vectors.tobytes())
                out.flush()
                done = start + len(vectors)
                if time.time() - last_report > 15 or done == len(texts):
                    last_report = time.time()
                    log.info("Embedded %d/%d chunks", done, len(texts))
    finally:
        client.close()
    matrix = np.fromfile(partial, dtype=np.float32).reshape(-1, dimension)
    if matrix.shape[0] != len(texts):
        raise RuntimeError(f"Expected {len(texts)} embeddings, found {matrix.shape[0]}")
    np.save(path, matrix)
    partial.unlink()
    meta.unlink()
    log.info("Embeddings ready: %d vectors of %d dimensions in %.0fs", matrix.shape[0], dimension,
             time.time() - started)
    return path


# ---------------------------------------------------------------- retrieval

@dataclass
class ScoredChunk:
    index: int
    score: float


class Retriever:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.catalog = Catalog.load(settings.data_dir)
        info = json.loads((settings.index_dir / "build_info.json").read_text(encoding="utf-8"))
        self.snapshot = info["snapshot"]
        self.info = info
        self.indexed_ids = set(info["document_ids"])
        self.chunks = _load_chunks(settings.index_dir)
        self.chunk_doc = np.array([c.document_id for c in self.chunks])
        self.chunk_authority = np.array([c.authority for c in self.chunks])
        self.doc_chunks: dict[str, list[int]] = {}
        for i, chunk in enumerate(self.chunks):
            self.doc_chunks.setdefault(chunk.document_id, []).append(i)
        self.bm25 = BM25([tokenize(index_text(c, self.catalog.get(c.document_id))) for c in self.chunks])
        self.docs = [self.catalog.get(d) for d in sorted(self.indexed_ids)]
        self.title_bm25 = BM25([tokenize(d.title) for d in self.docs])

        self.embeddings = None
        self.embedder = None
        self.dense_status = "off"           # off | ready | missing | degraded
        self.dense_failures = 0
        self._query_vectors: dict[str, np.ndarray] = {}
        if settings.dense_enabled:
            path = _embedding_path(settings)
            if not path.exists():
                log.warning("Dense retrieval is configured but the corpus is not fully embedded yet; "
                            "using keyword search only until a restart finishes embedding.")
                self.dense_status = "missing"
            else:
                embeddings = np.load(path)
                if embeddings.shape[0] != len(self.chunks):
                    raise RuntimeError("Embeddings do not match the chunk index; rebuild with --force.")
                self.embeddings = embeddings
                self.dense_status = "ready"
                # Queries are short and time-sensitive: fewer retries than indexing.
                self.embedder = EmbeddingClient(settings.embedding_base_url, settings.embedding_model,
                                                settings.embedding_api_key, settings.embedding_ca_cert,
                                                timeout=settings.request_timeout,
                                                max_tokens=settings.embedding_max_tokens, retries=2)
        self.reranker = (RerankClient(settings.rerank_base_url, settings.rerank_model, settings.rerank_api_key,
                                      settings.rerank_ca_cert, settings.request_timeout)
                         if settings.rerank_enabled else None)

    @classmethod
    def load_or_build(cls, settings: Settings) -> "Retriever":
        if settings.auto_build:
            build_index(settings)
        return cls(settings)

    def close(self):
        for client in (self.embedder, self.reranker):
            if client is not None:
                client.close()

    # -- core ranking -----------------------------------------------------

    def _candidate_mask(self, authority: str | None, document_ids: list[str] | None) -> np.ndarray:
        mask = np.ones(len(self.chunks), dtype=bool)
        if authority:
            mask &= self.chunk_authority == authority
        if document_ids is not None:
            mask &= np.isin(self.chunk_doc, document_ids)
        return mask

    def rank_chunks(self, query: str, authority: str | None = None,
                    document_ids: list[str] | None = None, top_k: int = 50,
                    rerank: bool = True) -> list[ScoredChunk]:
        mask = self._candidate_mask(authority, document_ids)
        if not mask.any():
            return []
        candidates = np.flatnonzero(mask)
        fused: dict[int, float] = {}

        lexical = self.bm25.scores(tokenize(query))[candidates]
        order = candidates[np.argsort(-lexical, kind="stable")]
        lexical_sorted = np.sort(lexical)[::-1]
        for rank, idx in enumerate(order[:LEXICAL_CANDIDATES]):
            if lexical_sorted[rank] <= 0:
                break
            fused[int(idx)] = fused.get(int(idx), 0.0) + 1.0 / (RRF_K + rank + 1)

        query_vector = self._query_vector(query) if self.embeddings is not None else None
        if query_vector is not None:
            dense = self.embeddings[candidates] @ query_vector
            dense_order = candidates[np.argsort(-dense, kind="stable")]
            for rank, idx in enumerate(dense_order[:DENSE_CANDIDATES]):
                fused[int(idx)] = fused.get(int(idx), 0.0) + 1.0 / (RRF_K + rank + 1)

        ranked = sorted((ScoredChunk(i, s) for i, s in fused.items()), key=lambda c: (-c.score, c.index))
        if rerank and self.reranker is not None and ranked:
            head = ranked[: self.settings.rerank_candidates]
            scores = self.reranker.rerank(query, [self.chunks[c.index].text for c in head])
            if scores is not None:
                head = [ScoredChunk(c.index, s) for c, s in zip(head, scores)]
                head.sort(key=lambda c: (-c.score, c.index))
                ranked = head + ranked[len(head):]
        return ranked[:top_k]

    def _query_vector(self, query: str) -> np.ndarray | None:
        """Embed a query once (search and fetch reuse it). If the embedding server fails,
        this question falls back to keyword search instead of failing."""
        cached = self._query_vectors.get(query)
        if cached is not None:
            return cached
        try:
            vector = self.embedder.embed([self.settings.embedding_query_prefix + query])[0]
        except (EmbeddingError, httpx.HTTPError) as exc:
            self.dense_failures += 1
            self.dense_status = "degraded"
            log.warning("Query embedding failed (%s); using keyword search for this question", exc)
            return None
        self.dense_status = "ready"
        if len(self._query_vectors) >= 256:
            self._query_vectors.pop(next(iter(self._query_vectors)))
        self._query_vectors[query] = vector
        return vector

    # -- agent tool contract -----------------------------------------------

    def search_documents(self, query: str, authority: str, limit: int = 3,
                         fiscal_year: str | None = None) -> dict:
        """Document-level hits for one authority. fiscal_year is accepted for contract
        compatibility; the catalog has no per-year metadata, so it does not filter."""
        if authority not in ("RBI", "TAX"):
            return {"hits": []}
        chunk_hits = self.rank_chunks(query, authority=authority, top_k=150)
        doc_rank: dict[str, list[int]] = {}
        for rank, hit in enumerate(chunk_hits):
            doc_rank.setdefault(self.chunks[hit.index].document_id, []).append(rank)

        scores: dict[str, float] = {}
        for doc_id, ranks in doc_rank.items():
            # best chunk dominates; further strong chunks add a little support
            scores[doc_id] = sum(w / (RRF_K + r + 1) for w, r in zip((1.0, 0.35, 0.15), ranks))

        title_scores = self.title_bm25.scores(tokenize(query))
        title_order = [i for i in np.argsort(-title_scores, kind="stable") if title_scores[i] > 0]
        rank = 0
        for i in title_order:
            doc = self.docs[i]
            if doc.authority != authority:
                continue
            scores[doc.document_id] = scores.get(doc.document_id, 0.0) + 0.5 / (RRF_K + rank + 1)
            rank += 1

        self._apply_priors(query, authority, scores)
        ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        top = ordered[0][1] if ordered else 1.0
        return {"hits": [{"document_id": d, "score": round(s / top, 6)} for d, s in ordered]}

    _ID_PATTERN = re.compile(r"\b(\d{1,3})\s*(?:/|of)\s*((?:19|20)\d{2})\b", re.I)
    _NBFC = re.compile(r"\bnbfcs?\b|non[-\s]?banking|housing finance|core investment|account aggregator|"
                       r"primary dealer|mortgage guarantee", re.I)
    _BANK = re.compile(r"\b(commercial|scheduled|small finance|payments?|urban co-?operative|"
                       r"regional rural|foreign|local area)?\s*banks?\b", re.I)

    def _apply_priors(self, query: str, authority: str, scores: dict[str, float]) -> None:
        """Query-understanding signals that BM25 handles poorly.

        * Instrument identifiers ("Notification 41/2022", "Act 22 of 2015") are a
          strong signal when they match a catalogued title.
        * For RBI, the regulated-entity type in the question (NBFC vs bank) decides
          which of two near-identical Directions applies.
        """
        if not scores:
            return
        top = max(scores.values())
        identifiers = {(int(n), y) for n, y in self._ID_PATTERN.findall(query)}
        if identifiers:
            for doc in self.docs:
                if doc.authority != authority:
                    continue
                title_ids = {(int(n), y) for n, y in self._ID_PATTERN.findall(doc.title)}
                if identifiers & title_ids:
                    scores[doc.document_id] = scores.get(doc.document_id, 0.0) + top
        if authority == "RBI":
            mentions_nbfc = bool(self._NBFC.search(query))
            mentions_bank = bool(self._BANK.search(re.sub(r"reserve bank", "", query, flags=re.I)))
            preferred = None
            if mentions_nbfc and not mentions_bank:
                preferred = "nbfc"
            elif mentions_bank and not mentions_nbfc:
                preferred = "commercial_banks"
            if preferred:
                for doc_id in scores:
                    if self.catalog.get(doc_id).collection == preferred:
                        scores[doc_id] *= 1.3

    def fetch_passages(self, document_ids: list[str], query: str | None = None) -> dict:
        for doc_id in document_ids:
            if doc_id not in self.indexed_ids:
                raise KeyError(doc_id)
        limit = min(self.settings.fetch_max_passages, 10)
        selected: list[int] = []
        if query:
            ranked = self.rank_chunks(query, document_ids=list(document_ids), top_k=60)
            per_doc: dict[str, list[int]] = {}
            for hit in ranked:
                per_doc.setdefault(self.chunks[hit.index].document_id, []).append(hit.index)
            # Every requested document gets one passage first (its best match, or its
            # opening when nothing matches), then remaining slots go by relevance.
            for doc_id in dict.fromkeys(document_ids):
                selected.append(per_doc[doc_id][0] if per_doc.get(doc_id) else self.doc_chunks[doc_id][0])
            for hit in ranked:
                if len(selected) >= limit:
                    break
                if hit.index not in selected:
                    selected.append(hit.index)
        else:
            share = max(1, limit // len(document_ids))
            for doc_id in document_ids:
                selected.extend(self.doc_chunks[doc_id][:share])
        selected = self._add_sibling_subsections(selected[:limit], limit)
        return {"passages": [self._passage(i) for i in selected]}

    _SUB_ID = re.compile(r"^([A-Z])\.(\d{1,2})\b")

    def _add_sibling_subsections(self, selected: list[int], limit: int) -> list[int]:
        """Show a parallel sub-section next to a retrieved one.

        RBI Directions sometimes split a rule by product or benchmark into parallel
        sub-sections ("B.1 Reset ... under MCLR", "B.2 Reset ... under External Benchmarks").
        Seeing only one invites applying its rule to the other's products. For the top two
        passages, the opening chunk of each sibling sub-section is added right after it,
        conservatively: only when the topic has at most four sub-sections (a genuine choice,
        not a long list), at most two additions in total, and never displacing any of the
        five highest-ranked passages.
        """
        result = list(selected)
        protected = set(selected[:5])
        added = 0
        for index in selected[:2]:
            chunk = self.chunks[index]
            own = self._SUB_ID.match(chunk.subheading or "")
            if not own:
                continue
            family: dict[str, int] = {}
            for other in self.doc_chunks[chunk.document_id]:
                sub = self.chunks[other].subheading or ""
                match = self._SUB_ID.match(sub)
                if match and match.group(1) == own.group(1):
                    family.setdefault(sub, other)  # first chunk of each sub-section
            if len(family) > 4:
                continue
            for sub, sibling in family.items():
                if sub == chunk.subheading or sibling in result or added >= 2:
                    continue
                if len(result) >= limit:
                    removable = [i for i in reversed(result) if i not in protected]
                    if not removable:
                        break
                    result.remove(removable[0])
                result.insert(result.index(index) + 1, sibling)
                added += 1
        return result[:limit]

    def _passage(self, index: int) -> dict:
        chunk = self.chunks[index]
        doc = self.catalog.get(chunk.document_id)
        url = doc.source_url
        url = f"{url}#page={chunk.page}"
        # Chapter, sub-section and paragraph, e.g. "Chapter IV … | B.1 Reset … (MCLR) system | para 36".
        # The sub-section tells the model which products or benchmarks a rule covers.
        section = " | ".join(part for part in (chunk.heading, chunk.subheading, chunk.section) if part)
        return {
            "citation_id": chunk.chunk_id,
            "document_id": doc.document_id,
            "authority": doc.authority,
            "title": doc.title,
            "text": chunk.text[:PASSAGE_TEXT_LIMIT],
            "page": chunk.page,
            "section": section[:300],
            "source_url": url,
            "corpus_snapshot": self.snapshot,
        }

    def lookup_document_status(self, document_ids: list[str], as_of: str,
                               fiscal_year: str | None = None) -> dict:
        requested = date.fromisoformat(as_of)  # ValueError -> HTTP 422
        results = []
        for doc_id in document_ids:
            doc = self.catalog.get(doc_id)
            if doc is None or doc_id not in self.indexed_ids:
                results.append({"document_id": doc_id, "status": "unknown", "reason": "Not in catalog."})
                continue
            start = date.fromisoformat(doc.effective_from) if doc.effective_from else None
            end = date.fromisoformat(doc.effective_to) if doc.effective_to else None
            if start is None:
                status, reason = "unknown", "Catalog has no effective date for this document."
            elif requested < start:
                status, reason = "not_applicable", f"Not yet in force on {as_of}: effective from {doc.effective_from}."
            elif end and requested >= end:
                status, reason = "not_applicable", f"No longer applicable on {as_of}: catalogued until {doc.effective_to}."
            else:
                status, reason = "applicable", f"In force on {as_of} according to catalog snapshot {self.snapshot}."
            if doc.note:
                reason = f"{reason} {doc.note}"
            results.append({
                "document_id": doc_id, "status": status, "reason": reason[:600],
                "effective_from": doc.effective_from, "effective_to": doc.effective_to,
                "superseded_by": doc.superseded_by,
            })
        return {"documents": results}
