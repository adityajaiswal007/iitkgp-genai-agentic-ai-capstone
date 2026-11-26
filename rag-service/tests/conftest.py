"""Fixtures: a small index built from real corpus files (fast, but realistic)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from regassist_rag.api import create_app
from regassist_rag.config import Settings
from regassist_rag.index import Retriever, build_index

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

SUBSET = {
    "24_CB_CreditDebitCards_2025_upd2026-10-01.pdf",
    "NBFC_NonBankingFinancialCompaniesCreditCardsIssuanceConduct_2025_id12956.pdf",
    "15_CB_IRACP_2025_upd2026-10-01.pdf",
    "04_CB_AssetClassificationProvisioning_2026.pdf",
    "Circular_3_2018.pdf",
    "Circular_17_2019.pdf",
    "Notification-41-2022.pdf",
    "A2015-22.pdf",
}


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    index_dir = tmp_path_factory.mktemp("index")
    s = Settings(data_dir=DATA_DIR, index_dir=index_dir, auto_build=False, ocr=False)
    build_index(s, only_filenames=SUBSET)
    return s


@pytest.fixture(scope="session")
def retriever(settings) -> Retriever:
    return Retriever(settings)


@pytest.fixture()
def client(settings, retriever):
    with TestClient(create_app(settings, retriever)) as c:
        yield c


def doc_id(retriever: Retriever, filename: str) -> str:
    return retriever.catalog.by_filename[filename]
