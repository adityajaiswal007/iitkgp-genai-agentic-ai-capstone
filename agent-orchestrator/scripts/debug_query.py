"""Run one question through the agent and show why it was or was not answered.

The API reports only counts for its automatic checks. This prints the actual
revision feedback, the draft claims with their citations, and the evidence labels.

  .venv\\Scripts\\python scripts\\debug_query.py "Your question here"

Run from agent-orchestrator with the retrieval service running. Uses the same
.env settings as the agent; keeps its state in debug_state.sqlite (git-ignored).
"""
import json
import sys
import uuid

from dotenv import load_dotenv

load_dotenv(".env")

from regulatory_agent.agent import RegulatoryAgent  # noqa: E402
from regulatory_agent.config import Settings  # noqa: E402
from regulatory_agent.grounding import label_evidence  # noqa: E402
from regulatory_agent.models import DemoModel, QwenModel  # noqa: E402
from regulatory_agent.tools import CorpusTools, DEMO_DOCUMENTS, HttpCorpusTools  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit('Usage: python scripts/debug_query.py "question"')
    settings = Settings.from_env()
    model = DemoModel() if settings.mode == "demo" else QwenModel()
    tools = (HttpCorpusTools(settings.retrieval_base_url, settings.retrieval_api_key, settings.timeout_seconds)
             if settings.retrieval_base_url else CorpusTools(DEMO_DOCUMENTS))
    agent = RegulatoryAgent(model, tools, "debug_state.sqlite", settings.limits())
    try:
        s = agent.ask("debug-" + uuid.uuid4().hex[:8], sys.argv[1])
    finally:
        agent.close()

    print("STATUS:", s["status"], "| mode:", settings.mode, "| authority:", s["plan"].get("authority"))
    print("QUERY: ", s["plan"].get("query"))
    _, labels = label_evidence(s.get("evidence", []), s.get("document_status", []))
    print("\nEVIDENCE")
    for tag, p in labels.items():
        print(f"  {tag} = {p['citation_id']}  ({p['section']})")
    print("\nSTATUS CHECKS")
    for d in s.get("document_status", []):
        print(f"  {d['document_id']}: {d['status']}")
    print("\nLAST DRAFT")
    for i, claim in enumerate(s.get("draft", {}).get("claims", []), 1):
        print(f"  Claim {i} {claim.get('citations')}: {claim.get('text')}")
    for item in s.get("draft", {}).get("limitations", []):
        print("  Limitation:", item)
    print("\nREVISION FEEDBACK (automatic checks first, then the critic)")
    for item in s.get("feedback", []) or ["(none)"]:
        print("  -", item)
    print("\nANSWER")
    print(json.dumps(s.get("answer", {}), ensure_ascii=False, indent=2)[:3000])


if __name__ == "__main__":
    main()
