"""Score the chatbot against eval/golden.jsonl.

  retrieval hit rate : the expected document is among the top-k retrieved chunks
  answer accuracy    : the answer contains any of the expected phrases (case-insensitive)
  groundedness (opt) : an LLM judge rates 1-5 whether every claim is supported by the sources

python scripts/evaluate.py [--judge] [--min-accuracy 0.8]
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.auth import DEMO_USERS  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.history import MemoryHistory  # noqa: E402
from app.models import User  # noqa: E402
from app.rag import RagService  # noqa: E402

JUDGE_PROMPT = """Rate from 1 to 5 how well the ANSWER is supported by the SOURCES \
(5 = every claim is supported, 1 = mostly unsupported). A correct "I could not find it" with no claims scores 5. \
Reply with the number only.

SOURCES:
{sources}

ANSWER:
{answer}"""


def run(service: RagService, case: dict) -> dict:
    name = case.get("user", "Admin")
    user = User(name=name, groups=DEMO_USERS[name])
    answer, sources = "", []
    for event in service.answer_stream(case["question"], str(uuid.uuid4()), user):
        if event["type"] == "sources":
            sources = event["sources"]
        elif event["type"] == "token":
            answer += event["text"]
    retrieved = {s["source"] for s in sources}
    return {
        **case,
        "answer": answer,
        "hit": case["source"] in retrieved if case["source"] else None,
        "correct": any(e.lower() in answer.lower() for e in case["expect"]),
        "sources": sources,
    }


def main() -> None:
    from app.llm import AzureLLM
    from app.retrieval.store import make_store

    parser = argparse.ArgumentParser()
    parser.add_argument("--judge", action="store_true", help="also score groundedness with the LLM")
    parser.add_argument("--min-accuracy", type=float, default=0.0, help="exit 1 below this (for CI)")
    args = parser.parse_args()

    settings = get_settings()
    llm = AzureLLM(settings)
    service = RagService(settings, llm, make_store(settings), MemoryHistory())
    cases = [json.loads(line) for line in (ROOT / "eval" / "golden.jsonl").read_text().splitlines() if line.strip()]

    results = []
    for case in cases:
        r = run(service, case)
        if args.judge:
            text = "\n\n".join(f"[{s['n']}] {s['snippet']}" for s in r["sources"])
            score, _ = llm.chat([{"role": "user", "content": JUDGE_PROMPT.format(sources=text, answer=r["answer"])}],
                                max_tokens=3)
            r["grounded"] = int("".join(ch for ch in score if ch.isdigit())[:1] or 0)
        mark = "PASS" if r["correct"] else "FAIL"
        print(f"{mark}  {case['question']}\n      -> {r['answer'][:160].replace(chr(10), ' ')}")
        results.append(r)

    hits = [r["hit"] for r in results if r["hit"] is not None]
    accuracy = sum(r["correct"] for r in results) / len(results)
    print("\n" + "=" * 60)
    print(f"Questions          : {len(results)}")
    print(f"Retrieval hit rate : {sum(hits) / len(hits):.0%}")
    print(f"Answer accuracy    : {accuracy:.0%}")
    if args.judge:
        print(f"Groundedness (1-5) : {sum(r['grounded'] for r in results) / len(results):.2f}")
    out = ROOT / "eval" / "results.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"Details saved to {out.relative_to(ROOT)}")
    sys.exit(1 if accuracy < args.min_accuracy else 0)


if __name__ == "__main__":
    main()
