#!/usr/bin/env python3
"""Extract per-page text for every PDF under the TaxDocuments folders.  Needs: pip install pymupdf
usage: python3 -I extract_text.py <TaxDocuments root> <out dir>
Output: <out>/<pdf name minus .pdf, first 60 chars>.txt with pages separated by \\f (PDF page = index+1)."""
import fitz, os, sys
root, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
for d, _, fs in os.walk(root):
    if "00_TaskB" in d: continue
    for f in fs:
        if f.lower().endswith(".pdf"):
            pages = [p.get_text() for p in fitz.open(os.path.join(d, f))]
            open(os.path.join(out, f[:-4][:60] + ".txt"), "w", encoding="utf-8").write("\n\f".join(pages))
            print(f, len(pages))
