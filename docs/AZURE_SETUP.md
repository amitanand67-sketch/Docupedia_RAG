# Azure setup

For local testing you only need **one** Azure service: Azure OpenAI. Search, document extraction,
chat history and sign-in all have local stand-ins until we deploy.

## Part 1: account housekeeping (about 10 minutes)

1. **Check your credit.** Sign in at <https://portal.azure.com>, open **Cost Management** and look at
   *Credits*. The $200 free credit lasts 30 days from sign-up, so a 5-day-old account has about
   25 days left.
2. **Create a resource group** (a folder for everything in this project, so one delete removes it all).
   Search **Resource groups** → **Create**.
   - Name: `rg-docupedia-dev`
   - Region: **East US 2** (widest choice of OpenAI models). Sweden Central is a good second choice.
3. **Set a budget alert.** **Cost Management** → **Budgets** → **Add**.
   - Scope: your subscription. Amount: **$20** per month.
   - Alerts: 50%, 90% and 100% of actual cost, sent to your email.

## Part 2: Azure OpenAI (needed for local testing)

4. **Create the resource.** Search **Azure OpenAI** → **Create**.
   - Resource group: `rg-docupedia-dev`, region: East US 2
   - Name: something globally unique, for example `aoai-docupedia-<your initials>`
   - Pricing tier: **Standard S0**. Network: *All networks* for now.
   - **Review + create**.

   If creation is blocked, or the model list later shows 0 quota, the free-trial subscription is the
   usual cause. Upgrading to pay-as-you-go (**Subscriptions** → your subscription → **Upgrade**) keeps
   your remaining credit and unlocks it. You still pay nothing until the credit is used up.

5. **Deploy two models.** Open the resource → **Go to Azure AI Foundry portal** → **Deployments** →
   **Deploy model** → **Deploy base model**.

   | Model | Deployment name | Deployment type | Tokens per minute |
   | --- | --- | --- | --- |
   | `gpt-4o-mini` (or `gpt-4.1-mini` if 4o-mini isn't listed) | same as the model | Global Standard | 30K |
   | `text-embedding-3-small` | same as the model | Standard or Global Standard | 30K |

   The chat model must accept images (both models above do), because ingestion describes the
   pictures inside documents. Avoid reasoning models such as o-series or gpt-5 here: they reject the
   `temperature` setting this code uses.

6. **Copy the endpoint and key.** Resource → **Keys and Endpoint** → copy *Endpoint* and *KEY 1*.
   Treat the key like a password: it goes only in your local `.env` file, which git ignores.

## Part 3: run it in VS Code

7. Install [Python 3.11 or newer](https://www.python.org/downloads/), [VS Code](https://code.visualstudio.com/)
   with the Python extension, and [Git](https://git-scm.com/).
8. Open the `docupedia-rag` folder in VS Code, then in the terminal (**Terminal** → **New Terminal**):

   ```bash
   python -m venv .venv
   # Windows:      .venv\Scripts\activate
   # macOS/Linux:  source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```

9. Copy `.env.example` to `.env` and fill in `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY` and the two
   deployment names from step 5.
10. Check the connection, run the tests, index the sample documents and start the app:

    ```bash
    python scripts/check_azure.py            # should print OK twice
    python -m pytest                         # offline tests, no Azure calls
    python scripts/ingest.py sample_docs --reset
    uvicorn app.main:app --reload            # open http://localhost:8000
    python scripts/evaluate.py               # accuracy report on 20 test questions
    ```

    Indexing the sample documents and running the evaluation together cost a few cents.

## Part 4: deploy

When the local version works, follow [DEPLOY_AZURE.md](DEPLOY_AZURE.md). It uses its own settings file,
`.env.azure`, and one script, `deploy/azure.sh`, that creates and publishes everything.
