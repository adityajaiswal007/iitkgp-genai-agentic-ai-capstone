from fastapi.testclient import TestClient

from regassist_rag.api import create_app
from regassist_rag.config import Settings

from conftest import doc_id


def test_health_reports_snapshot_and_corpus(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["snapshot"].startswith("regassist-")
    assert body["documents"]["RBI"] == 4 and body["documents"]["TAX"] == 4
    assert body["dense"] is False


def test_search_finds_the_right_rbi_direction(client, retriever):
    hits = client.post("/v1/tools/search_documents", json={
        "query": "Within how many days must a bank close a credit card on request?",
        "authority": "RBI", "limit": 3}).json()["hits"]
    assert hits[0]["document_id"] == doc_id(retriever, "24_CB_CreditDebitCards_2025_upd2026-10-01.pdf")
    assert set(hits[0]) == {"document_id", "score"}


def test_entity_type_prefers_nbfc_directions(client, retriever):
    hits = client.post("/v1/tools/search_documents", json={
        "query": "What does an NBFC need before it can issue credit cards?", "authority": "RBI"}).json()["hits"]
    assert hits[0]["document_id"] == doc_id(
        retriever, "NBFC_NonBankingFinancialCompaniesCreditCardsIssuanceConduct_2025_id12956.pdf")


def test_instrument_identifier_ranks_the_notification_first(client):
    hits = client.post("/v1/tools/search_documents", json={
        "query": "Under Notification 41/2022 which court is the Special Court for Coimbatore?",
        "authority": "TAX"}).json()["hits"]
    assert hits[0]["document_id"] == "tax-notification-41-2022"


def test_search_is_scoped_to_the_requested_authority(client):
    rbi = client.post("/v1/tools/search_documents", json={"query": "monetary limits appeals", "authority": "RBI"}).json()
    assert all(h["document_id"].startswith("rbi-") for h in rbi["hits"])
    sebi = client.post("/v1/tools/search_documents", json={"query": "mutual funds", "authority": "SEBI"}).json()
    assert sebi == {"hits": []}


def test_fetch_returns_cited_passages_with_page_and_section(client, retriever):
    card = doc_id(retriever, "24_CB_CreditDebitCards_2025_upd2026-10-01.pdf")
    body = client.post("/v1/tools/fetch_passages", json={
        "document_ids": [card], "query": "closure of credit card seven working days"}).json()
    top = body["passages"][0]
    assert "seven working days" in top["text"]
    assert top["page"] == 11 and "para 19" in top["section"]
    assert top["source_url"].endswith("#page=11") and top["citation_id"].startswith(card + ":p11:")
    assert len(body["passages"]) <= 8 and all(len(p["text"]) <= 6000 for p in body["passages"])


def test_fetch_gives_each_requested_document_a_passage(client, retriever):
    ids = [doc_id(retriever, "24_CB_CreditDebitCards_2025_upd2026-10-01.pdf"), "tax-circular-3-2018"]
    passages = client.post("/v1/tools/fetch_passages", json={"document_ids": ids, "query": "credit card"}).json()["passages"]
    assert {p["document_id"] for p in passages} == set(ids)


def test_fetch_unknown_document_is_404(client):
    response = client.post("/v1/tools/fetch_passages", json={"document_ids": ["rbi-999-nope"]})
    assert response.status_code == 404


def test_status_reflects_future_effective_and_repealed_directions(client, retriever):
    new = doc_id(retriever, "04_CB_AssetClassificationProvisioning_2026.pdf")
    old = doc_id(retriever, "15_CB_IRACP_2025_upd2026-10-01.pdf")

    def status(as_of):
        docs = client.post("/v1/tools/lookup_document_status",
                           json={"document_ids": [new, old], "as_of": as_of}).json()["documents"]
        return {d["document_id"]: d["status"] for d in docs}

    assert status("2026-11-26") == {new: "not_applicable", old: "applicable"}
    assert status("2027-05-01") == {new: "applicable", old: "not_applicable"}


def test_status_unknown_id_and_bad_date(client):
    docs = client.post("/v1/tools/lookup_document_status",
                       json={"document_ids": ["rbi-999-nope"], "as_of": "2026-11-26"}).json()["documents"]
    assert docs == [{"document_id": "rbi-999-nope", "status": "unknown", "reason": "Not in catalog."}]
    bad = client.post("/v1/tools/lookup_document_status", json={"document_ids": ["x"], "as_of": "26/11/2026"})
    assert bad.status_code == 422


def test_requests_are_validated(client):
    assert client.post("/v1/tools/search_documents", json={"query": "x", "authority": "FED"}).status_code == 422
    assert client.post("/v1/tools/search_documents", json={"query": "x", "authority": "RBI", "limit": 9}).status_code == 422
    assert client.post("/v1/tools/fetch_passages", json={"document_ids": ["a"] * 6}).status_code == 422


def test_api_key_is_enforced_when_configured(settings, retriever):
    keyed = Settings(**{**settings.__dict__, "api_key": "secret"})
    with TestClient(create_app(keyed, retriever)) as c:
        body = {"query": "credit card", "authority": "RBI"}
        assert c.post("/v1/tools/search_documents", json=body).status_code == 401
        assert c.post("/v1/tools/search_documents", json=body, headers={"X-API-Key": "secret"}).status_code == 200
        assert c.get("/health").status_code == 200


def test_chunk_search_endpoint_for_evaluation(client):
    body = client.post("/v1/search_chunks", json={"query": "Special Court Coimbatore", "authority": "TAX", "top_k": 3}).json()
    assert len(body["hits"]) == 3 and body["hits"][0]["document_id"] == "tax-notification-41-2022"


def test_sibling_sub_sections_are_fetched_together(settings, retriever):
    # When one sub-section is retrieved, its parallel sub-sections come with it, so the
    # model can see which rule covers which product (MCLR vs external benchmark).
    from regassist_rag.chunking import Chunk
    r = retriever
    doc = next(iter(r.doc_chunks))
    base = r.doc_chunks[doc][0]
    saved = (list(r.chunks), dict(r.doc_chunks))
    try:
        b1 = Chunk("x:b1", doc, "RBI", 1, 900, "para 36", "", "36. one year or lower", "B.1 Reset under MCLR")
        b2 = Chunk("x:b2", doc, "RBI", 1, 901, "para 38", "", "38. three months", "B.2 Reset under External Benchmarks")
        r.chunks.extend([b1, b2])
        i1, i2 = len(r.chunks) - 2, len(r.chunks) - 1
        r.doc_chunks[doc] = r.doc_chunks[doc] + [i1, i2]
        result = r._add_sibling_subsections([i1, base], limit=8)
        assert result[:2] == [i1, i2]
        assert r._add_sibling_subsections([base], limit=8) == [base]  # no sub-section, nothing added
    finally:
        r.chunks[:] = saved[0]
        r.doc_chunks.clear()
        r.doc_chunks.update(saved[1])
