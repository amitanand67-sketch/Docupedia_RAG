# Deploy to Azure

This is the only guide you need for the cloud version. Everything Azure-specific lives in two files:

| File | What it is |
| --- | --- |
| `.env.azure` | All settings and keys for the cloud app. Copied from `.env.azure.example`; git ignores it. |
| `deploy/azure.sh` | One script that creates the resources, indexes the documents and publishes the app. |

Your `.env` is untouched and keeps running the laptop version.

## What you'll end up with

```text
Browser ──> Container Apps (FastAPI + UI, sleeps when idle)
               ├── Azure OpenAI        gpt-4.1-mini + text-embedding-3-small (your existing resource)
               ├── Azure AI Search     the index: vectors + keywords, group filter   (Free)
               └── Cosmos DB           conversations, feedback, analytics            (free tier)
Laptop ──ingest──> Azure AI Search     documents are indexed from your Mac
```

## Cost

| Resource | Tier | Cost |
| --- | --- | --- |
| Azure AI Search | Free (50 MB, 3 indexes) | $0 |
| Cosmos DB for NoSQL | Free tier discount (1000 RU/s, 25 GB) | $0 |
| Container Apps | Consumption, scales to zero | $0 at demo traffic (180,000 vCPU-seconds free each month) |
| Container Registry | Basic | about $0.17 a day, $5 a month |
| Azure OpenAI | Pay per token | a few cents a day of demos |

So expect about **$5 to $10 a month**, paid from the $200 credit while it lasts (until about 18 November).
The rate limits in `.env.azure` (20 questions per visitor per hour, 300 a day in total) cap what a
public link can spend on Azure OpenAI.

When the credit ends, Azure disables the resources unless you upgrade to pay-as-you-go. Decide before
then whether to upgrade or run `./deploy/azure.sh delete`.

## Before you start (one time)

```bash
brew install azure-cli          # skip if `az version` already works
az login                        # opens the browser; pick the subscription with your credit
```

Install [Docker Desktop](https://www.docker.com/products/docker-desktop) too. The script first tries
to build the image inside Azure, but free-credit subscriptions often aren't allowed to, and then it
builds on your Mac with Docker instead.

## Step 1: fill in .env.azure

```bash
cp .env.azure.example .env.azure
```

Open `.env.azure` and fill in **section 1** only: paste `AZURE_OPENAI_API_KEY` from your `.env`. The
other values are already right for you. Leave section 3 empty; the script fills it in.

## Step 2: create the resources (about 15 minutes)

```bash
./deploy/azure.sh create
```

It lists what it will create and what it costs, and waits for `y`. Cosmos DB is the slow one (5 to 10
minutes). When it finishes, `.env.azure` section 3 holds the endpoints and keys.

If it stops with an error, read the message: the two common ones are "a free search service already
exists" (only one per subscription) and "no capacity in this region" (change `AZURE_LOCATION`, for
example to `eastus`, and run `create` again). Running `create` again is safe; it skips what exists.

## Step 3: index the documents into Azure AI Search (2 minutes)

```bash
./deploy/azure.sh ingest
```

This runs the same ingestion as on your laptop (extract, describe images, chunk, embed) but writes to
Azure AI Search. You should see `Indexed ... chunks from 3 documents`. In the Azure portal, open the
search service, **Indexes > docupedia > Search** to look at the chunks.

## Step 4: publish the app (5 minutes)

```bash
./deploy/azure.sh deploy
```

It builds the Docker image, pushes it to the registry, starts the Container App with your keys stored
as secrets, and prints the link:

```text
==> Live at https://docupedia-app.<random>.eastus2.azurecontainerapps.io
```

Open it and run the demo script from the README. The first visit after a quiet period takes 10 to 20
seconds because the app sleeps when nobody uses it; that's what keeps it free.

**After you change the code:** run `./deploy/azure.sh deploy` again.
**After you change documents:** run `./deploy/azure.sh ingest`, then `deploy` (the image carries the
files that citation links open).

## Everyday commands

| Command | What it does |
| --- | --- |
| `./deploy/azure.sh status` | The link, the running image, and the resources in the group |
| `./deploy/azure.sh logs` | Live logs: every question, retrieval time, tokens. Ctrl+C stops |
| `./deploy/azure.sh deploy` | Ship new code or new settings from `.env.azure` |
| `./deploy/azure.sh delete` | Delete everything the script created (asks you to type the group name) |

`delete` refuses to run if the resource group also holds an Azure OpenAI resource, so your models
are safe. That's why `.env.azure` uses its own group, `rg-docupedia-app`.

## Optional: Microsoft sign-in

The default (`AUTH_MODE=demo`) lets anyone with the link try it and switch between demo users, which
is what you want for interviewers. To show real enterprise sign-in instead:

```bash
./deploy/azure.sh signin        # turn on
./deploy/azure.sh signin off    # back to the demo picker
```

Visitors then sign in with an account in your Azure directory; people outside it must be invited as
guests (Entra ID > Users > Invite external user). Put your own email in `ADMIN_USERS` and run
`deploy` to see the Analytics tab.

## Optional: Document Intelligence

Set `WITH_DOCINTEL=true` and `EXTRACTOR=docintel` in `.env.azure`, run `create`, then `ingest`. The free
F0 tier only reads the first 2 pages of each document, so for the sample documents the local
extractor (pdfplumber, python-docx) is the better choice. Mention in interviews that Document
Intelligence is what you'd use for scanned PDFs and complex layouts.

## How the settings map to Azure

| Setting | Laptop (`.env`) | Cloud (`.env.azure`) |
| --- | --- | --- |
| `SEARCH_BACKEND` | `local` JSON index | `azure` AI Search |
| `HISTORY_BACKEND` | `memory` (file in `.data/`) | `cosmos` |
| `AUTH_MODE` | `demo` | `demo`, or `entra` after `signin` |
| `RATE_LIMIT_PER_HOUR`, `DAILY_QUESTION_LIMIT` | 0 (off) | 20 and 300 |

The app reads `.env` by default. `ENV_FILE=.env.azure` makes it read the Azure file instead, which is
how `ingest` writes to Azure AI Search from your Mac. In the cloud the script passes the same values
as environment variables, and the keys as Container Apps secrets.
