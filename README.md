# DocupediaAI: a RAG knowledge assistant on Azure

Ask questions about company documents and get answers with numbered citations. Modelled on an
enterprise "Docupedia" assistant: Azure OpenAI, LangChain, Azure AI Search and Document Intelligence,
with multi-turn chat, role-based access, feedback and an analytics page.

The demo company is **Lumora Systems** (fictional). Its documents in `sample_docs/` deliberately keep
some facts only inside images (the holiday calendar, the leave approval flowchart, the architecture
diagram), so the demo shows the bot reading pictures as well as text.

## How it works

```
INGESTION (scripts/ingest.py)
  PDF / Word / web page
    -> extract text   (pypdf, python-docx, BeautifulSoup  |  Azure Document Intelligence)
    -> describe images with the vision model (calendar, diagrams, org chart)
    -> split by headings into chunks of up to 512 tokens; tables keep their header row
    -> embed each chunk (text-embedding-3-small)
    -> store text + vector + access groups   (local JSON index  |  Azure AI Search)

QUESTION (app/rag.py)
  "and for sick leave?"
    -> rewrite follow-ups into a standalone query using chat history
    -> hybrid search: vector + keyword, merged with Reciprocal Rank Fusion,
       filtered to the documents this user's groups may see
    -> prompt: rules + numbered sources + history + question
    -> stream the answer with [1][2] citations to the browser
    -> log latency, tokens, sources; collect thumbs up/down
```

Every Azure service has a local stand-in, switched in `.env`, so you can build and test on your laptop
with only an Azure OpenAI resource:

| Concern | Local (default) | Azure | Setting |
| --- | --- | --- | --- |
| Search index | `.data/index.json`, hybrid BM25 + vector | Azure AI Search | `SEARCH_BACKEND` |
| Text extraction | pypdf, python-docx, BeautifulSoup | Document Intelligence layout model | `EXTRACTOR` |
| Chat history and analytics | in memory | Cosmos DB | `HISTORY_BACKEND` |
| Sign-in | demo user picker | Entra ID via Container Apps auth | `AUTH_MODE` |
| Monitoring | console logs | Application Insights | `APPLICATIONINSIGHTS_CONNECTION_STRING` |

## Quick start

Step-by-step Azure setup for local testing: [docs/AZURE_SETUP.md](docs/AZURE_SETUP.md).
Deploying to Azure with a public link: [docs/DEPLOY_AZURE.md](docs/DEPLOY_AZURE.md) (uses `.env.azure` and `deploy/azure.sh`).

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-dev.txt
copy .env.example .env            # macOS/Linux: cp .env.example .env, then fill in Azure OpenAI values

python scripts/check_azure.py
python -m pytest
python scripts/ingest.py sample_docs --reset
uvicorn app.main:app --reload     # http://localhost:8000
```

In VS Code you can also use **Run and Debug** → *Ingest sample_docs*, then *Run chatbot (uvicorn)*.

## Demo script (3 minutes)

1. **Text answer with a citation:** "How many days of earned leave do I get?" Click the [1] chip.
2. **Answer from an image:** "What are the company holidays in December 2027?" The source card shows
   an *image* badge: the dates exist only in the calendar picture.
3. **Flowchart reasoning:** "Who approves a 7-day leave request, and how long does it take?"
4. **Follow-up:** "and if it's only 3 days?" The UI shows the rewritten search query.
5. **Role-based access:** as *Asha (employee)*, ask "Which API gateway does OrbitDesk use?" and get no
   answer. Switch to *Ravi (engineer)* and ask again: the architecture doc is restricted to engineering
   in `sample_docs/access.json`.
6. **Refusal:** "What is the CEO's favourite food?" The bot says it can't find it instead of guessing.
7. **Analytics:** switch to *Admin* and open the Analytics tab: questions, latency, tokens, satisfaction.

## Quality checks

- `python -m pytest`: 19 offline tests with a fake model (extraction, heading- and table-aware chunking,
  hybrid search, security trimming, streaming API, history, rate limits, feedback, analytics, Entra headers).
- `python scripts/evaluate.py --judge`: runs the 20 questions in `eval/golden.jsonl` against the real
  model and reports retrieval hit rate, answer accuracy and LLM-judged groundedness.
  `--min-accuracy 0.85` makes it fail CI when quality drops.

## Project layout

```
app/
  config.py            settings from .env (or .env.azure with ENV_FILE=.env.azure)
  llm.py               Azure OpenAI: embeddings, chat, streaming, image description
  ingestion/           extract.py, chunker.py, pipeline.py
  retrieval/store.py   LocalStore and AzureSearchStore (hybrid search + group filter)
  rag.py               query rewriting, prompt, cited streaming answer
  history.py           chat memory, answer logs, feedback (memory or Cosmos DB)
  auth.py              demo users or Entra ID headers
  main.py              FastAPI: /api/chat (SSE), /api/feedback, /api/analytics, /files
  static/              chat UI (HTML, CSS, vanilla JS)
scripts/               ingest.py, check_azure.py, evaluate.py, make_sample_docs.py
sample_docs/           demo PDF, Word file, web page, images, access.json
eval/golden.jsonl      evaluation questions
tests/                 pytest suite
deploy/azure.sh        create Azure resources, index, publish, logs, delete
docs/                  AZURE_SETUP.md (local), DEPLOY_AZURE.md (cloud)
```

## Using your own documents

Drop PDFs, Word files, HTML or Markdown into a folder and run
`python scripts/ingest.py path/to/folder`, or pass URLs. Add an `access.json` in that folder to
restrict files to groups. To regenerate the Lumora samples: `python scripts/make_sample_docs.py`.
