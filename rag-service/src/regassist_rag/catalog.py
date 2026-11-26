"""Trusted document catalog built from the RBI manifest and the tax catalog.

The catalog is the only source of document IDs, titles, URLs and effective
dates. Nothing in it is inferred from user input or model output.
"""
from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

RBI_COLLECTIONS = {"commercial_banks": "documents/commercial_banks", "nbfc": "documents/nbfc"}


@dataclass(frozen=True)
class Document:
    document_id: str
    authority: str           # 'RBI' or 'TAX'
    collection: str          # commercial_banks, nbfc, or the tax folder name
    title: str
    filename: str
    path: str                # relative to the data directory
    doc_type: str
    issued: str | None
    effective_from: str | None
    effective_to: str | None
    superseded_by: str | None
    source_url: str
    in_scope: bool
    note: str
    sha256: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def _clean_date(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    date.fromisoformat(value)  # validate; raises on bad catalog data
    return value


def rbi_document_id(seq: str, filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    stem = re.sub(r"^\d+_", "", stem)
    stem = stem.replace("NonBankingFinancialCompanies", "")
    parts = [p for p in stem.split("_") if p and not re.match(r"^(\d{4}|upd.*|eff.*|id\d+)$", p)]
    slug = "-".join(parts).lower()
    slug = re.sub(r"[^a-z0-9-]+", "", slug)[:40].strip("-")
    return f"rbi-{int(seq):03d}-{slug}"


def load_rbi(data_dir: Path) -> list[Document]:
    root = data_dir / "RBI"
    overrides = {}
    if (root / "catalog_overrides.csv").exists():
        overrides = {row["filename"]: row for row in _read_csv(root / "catalog_overrides.csv")}
    rows = _read_csv(root / "manifest_frozen.csv")
    ids = {row["filename"]: rbi_document_id(row["seq"], row["filename"]) for row in rows}
    docs = []
    for row in rows:
        filename = row["filename"]
        collection = "nbfc" if filename.startswith("NBFC_") else "commercial_banks"
        rel = f"RBI/{RBI_COLLECTIONS[collection]}/{filename}"
        if not (data_dir / rel).exists():
            raise FileNotFoundError(f"Manifest lists a missing file: {rel}")
        override = overrides.get(filename, {})
        effective_from = _clean_date(override.get("effective_from")) or _clean_date(row.get("issued"))
        superseded_file = (override.get("superseded_by_filename") or "").strip()
        notes = [f"Manifest status: {row.get('status', '').strip()}."] if row.get("status") else []
        if row.get("updated_as_on"):
            notes.append(f"Text updated as on {row['updated_as_on']}.")
        if override.get("catalog_note"):
            notes.append(override["catalog_note"].strip())
        docs.append(Document(
            document_id=ids[filename],
            authority="RBI",
            collection=collection,
            title=row["title"].strip(),
            filename=filename,
            path=rel,
            doc_type="master_direction",
            issued=_clean_date(row.get("issued")),
            effective_from=effective_from,
            effective_to=_clean_date(override.get("effective_to")),
            superseded_by=ids.get(superseded_file) if superseded_file else None,
            source_url=row.get("pdf_url", "").strip() or row.get("page_url", "").strip(),
            in_scope=True,
            note=" ".join(notes),
            sha256=row.get("sha256", "").strip(),
        ))
    return docs


def load_tax(data_dir: Path) -> list[Document]:
    root = data_dir / "Tax"
    rows = _read_csv(root / "tax_catalog.csv")
    ids = {row["document_id"] for row in rows}
    docs = []
    for row in rows:
        rel = f"Tax/documents/{row['folder']}/{row['filename']}"
        if not (data_dir / rel).exists():
            raise FileNotFoundError(f"Tax catalog lists a missing file: {rel}")
        superseded = (row.get("superseded_by") or "").strip() or None
        if superseded and superseded not in ids:
            raise ValueError(f"Unknown superseded_by id {superseded!r} in tax catalog")
        docs.append(Document(
            document_id=row["document_id"].strip(),
            authority="TAX",
            collection=row["folder"],
            title=row["title"].strip(),
            filename=row["filename"],
            path=rel,
            doc_type=row["doc_type"].strip(),
            issued=_clean_date(row.get("issued")),
            effective_from=_clean_date(row.get("effective_from")),
            effective_to=_clean_date(row.get("effective_to")),
            superseded_by=superseded,
            source_url=f"urn:regassist:tax:{row['document_id'].strip()}",
            in_scope=row.get("in_scope", "").strip().lower() == "yes",
            note=(row.get("catalog_note") or "").strip(),
        ))
    return docs


def load_tax_aliases(data_dir: Path) -> dict[str, str]:
    aliases = {}
    for row in _read_csv(data_dir / "Tax" / "tax_catalog.csv"):
        for alias in (row.get("aliases") or "").split(";"):
            if alias.strip():
                aliases[alias.strip()] = row["document_id"].strip()
    return aliases


class Catalog:
    def __init__(self, documents: list[Document], aliases: dict[str, str] | None = None):
        self.documents = {d.document_id: d for d in documents}
        if len(self.documents) != len(documents):
            raise ValueError("Duplicate document IDs in catalog")
        self.by_filename = {d.filename: d.document_id for d in documents}
        # Other file names for the same document (e.g. the names used in the gold sets).
        for alias, document_id in (aliases or {}).items():
            self.by_filename.setdefault(alias, document_id)
        # The duplicate Black Money Act file is never indexed; map it to the original.
        for d in documents:
            if d.doc_type == "duplicate" and d.superseded_by:
                self.by_filename[d.filename] = d.superseded_by

    @classmethod
    def load(cls, data_dir: Path) -> "Catalog":
        return cls(load_rbi(data_dir) + load_tax(data_dir), load_tax_aliases(data_dir))

    def indexable(self, include_out_of_scope: bool = False) -> list[Document]:
        return [d for d in self.documents.values()
                if d.doc_type != "duplicate" and (d.in_scope or include_out_of_scope)]

    def get(self, document_id: str) -> Document | None:
        return self.documents.get(document_id)
