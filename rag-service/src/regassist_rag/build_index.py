"""Build the retrieval index:  python -m regassist_rag.build_index [--force]"""
from __future__ import annotations

import argparse
import json
import logging

from .config import Settings
from .index import build_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="rebuild even if the snapshot is unchanged")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    info = build_index(Settings.from_env(), force=args.force)
    summary = {k: v for k, v in info.items() if k != "document_ids"}
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
