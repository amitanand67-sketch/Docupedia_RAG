"""Index documents: python scripts/ingest.py sample_docs [more files, folders or URLs] [--reset]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.ingestion.pipeline import ingest  # noqa: E402
from app.llm import AzureLLM  # noqa: E402
from app.retrieval.store import make_store  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("locations", nargs="+", help="files, folders or http(s) URLs")
    parser.add_argument("--reset", action="store_true", help="local store only: delete the index first")
    parser.add_argument("--no-images", action="store_true", help="skip describing images (saves tokens)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    for noisy in ("azure", "httpx"):  # HTTP request logs; errors still show
        logging.getLogger(noisy).setLevel(logging.WARNING)
    settings = get_settings()
    if args.no_images:
        settings.describe_images = False
    if args.reset and settings.search_backend == "local":
        settings.local_index_path.unlink(missing_ok=True)

    store = make_store(settings)
    stats = ingest(args.locations, settings, AzureLLM(settings), store)
    print(f"\nIndexed {sum(stats.values())} chunks from {len(stats)} documents. Index now holds {store.count()}.")


if __name__ == "__main__":
    main()
