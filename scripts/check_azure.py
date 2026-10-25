"""Check that your .env settings reach every Azure service you turned on.

python scripts/check_azure.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402


def check(name: str, fn) -> bool:
    try:
        detail = fn()
        print(f"  OK    {name}: {detail}")
        return True
    except Exception as exc:  # show the real error, it usually names the fix
        print(f"  FAIL  {name}: {type(exc).__name__}: {exc}")
        return False


def main() -> None:
    from app.llm import AzureLLM

    s = get_settings()
    print(f"Endpoint: {s.azure_openai_endpoint or '(not set)'}\n")
    llm = AzureLLM(s)
    results = [
        check(f"chat deployment '{s.azure_openai_chat_deployment}'",
              lambda: llm.chat([{"role": "user", "content": "Reply with the word ready."}], max_tokens=5)[0]),
        check(f"embedding deployment '{s.azure_openai_embedding_deployment}'",
              lambda: f"{len(llm.embed(['hello'])[0])} dimensions"),
    ]
    if s.search_backend == "azure":
        from app.retrieval.store import AzureSearchStore

        results.append(check("Azure AI Search", lambda: f"{AzureSearchStore(s).count()} documents in index"))
    if s.history_backend == "cosmos":
        from app.history import CosmosHistory

        results.append(check("Cosmos DB", lambda: (CosmosHistory(s), "container ready")[1]))
    if s.extractor == "docintel":
        from app.ingestion.extract import _docintel

        sample = Path(__file__).resolve().parent.parent / "sample_docs" / "Lumora_Employee_Handbook_2027.pdf"
        results.append(check("Document Intelligence", lambda: f"{len(_docintel(sample.read_bytes(), s))} pages read"))
    print("\nAll good." if all(results) else "\nFix the FAIL lines above, then run this again.")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
