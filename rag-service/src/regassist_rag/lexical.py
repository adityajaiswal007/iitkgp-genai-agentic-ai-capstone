"""Tokenisation and a small, dependency-free BM25 index."""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from functools import lru_cache

import numpy as np
import snowballstemmer

_STEMMER = snowballstemmer.stemmer("english")
_TOKEN = re.compile(r"[a-z]+|\d+(?:\.\d+)?|[\u0900-\u097f]+")

STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does for from had has have how i if in into is it its
may might must of on or our shall should so such than that the their them then there these they this those to
under upon was we were what when where whether which while who whom why will with within would you your
""".split())


@lru_cache(maxsize=200_000)
def _stem(word: str) -> str:
    return _STEMMER.stemWord(word)


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"₹|\brs\.", " rs ", text)
    tokens = []
    for token in _TOKEN.findall(text):
        if token in STOPWORDS or (len(token) == 1 and not token.isdigit()):
            continue
        tokens.append(_stem(token) if token.isalpha() and token.isascii() else token)
    return tokens


class BM25:
    def __init__(self, documents: list[list[str]], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.n = len(documents)
        self.lengths = np.array([len(d) for d in documents], dtype=np.float32)
        self.avg_length = float(self.lengths.mean()) if self.n else 0.0
        postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for index, tokens in enumerate(documents):
            for term, tf in Counter(tokens).items():
                postings[term].append((index, tf))
        self.postings = {
            term: (np.fromiter((i for i, _ in plist), dtype=np.int32, count=len(plist)),
                   np.fromiter((tf for _, tf in plist), dtype=np.float32, count=len(plist)))
            for term, plist in postings.items()
        }
        self.idf = {term: math.log(1 + (self.n - len(ids) + 0.5) / (len(ids) + 0.5))
                    for term, (ids, _) in self.postings.items()}
        self._norm = self.k1 * (1 - self.b + self.b * self.lengths / max(self.avg_length, 1e-9))

    def scores(self, query_tokens: list[str]) -> np.ndarray:
        scores = np.zeros(self.n, dtype=np.float32)
        for term in set(query_tokens):
            entry = self.postings.get(term)
            if entry is None:
                continue
            ids, tf = entry
            scores[ids] += self.idf[term] * tf * (self.k1 + 1) / (tf + self._norm[ids])
        return scores
