"""All settings come from environment variables (or a local .env file).

Every Azure dependency has a local stand-in, so the app runs in VS Code with only
an Azure OpenAI resource:

    SEARCH_BACKEND=local     -> JSON vector store on disk instead of Azure AI Search
    EXTRACTOR=local          -> pypdf / python-docx / BeautifulSoup instead of Document Intelligence
    HISTORY_BACKEND=memory   -> chat history in a local JSON file instead of Cosmos DB
    AUTH_MODE=demo           -> pick a demo user in the UI instead of Entra ID sign-in

Settings are read from .env by default. Set ENV_FILE=.env.azure to use the Azure settings file
instead, for example when indexing documents into Azure AI Search from your laptop.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / os.environ.get("ENV_FILE", ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    # Azure OpenAI (required). Leave the key empty to sign in with Entra ID (az login / managed identity).
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_chat_deployment: str = "gpt-4o-mini"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536

    # Retrieval
    search_backend: Literal["local", "azure"] = "local"
    local_index_path: Path = ROOT / ".data" / "index.json"
    azure_search_endpoint: str = ""
    azure_search_api_key: str = ""
    azure_search_index: str = "docupedia"
    use_semantic_ranker: bool = False
    top_k: int = 5

    # Ingestion
    extractor: Literal["local", "docintel"] = "local"
    azure_docintel_endpoint: str = ""
    azure_docintel_api_key: str = ""
    describe_images: bool = True
    chunk_tokens: int = 512
    chunk_overlap_tokens: int = 64
    docs_dir: Path = ROOT / "sample_docs"

    # Chat history, feedback and analytics events
    history_backend: Literal["memory", "cosmos"] = "memory"
    cosmos_endpoint: str = ""
    cosmos_key: str = ""
    cosmos_database: str = "docupedia"
    cosmos_container: str = "conversations"
    local_history_path: Path = ROOT / ".data" / "history.json"
    # Messages of earlier conversation sent to the model (12 = the last 6 questions and their answers)
    history_turns: int = 12

    # Auth: "demo" shows a user picker in the UI; "entra" trusts Azure Container Apps / App Service sign-in headers
    auth_mode: Literal["demo", "entra"] = "demo"
    admin_group: str = "admin"
    # With AUTH_MODE=entra: comma-separated sign-in emails that get admin and engineering access
    admin_users: str = ""

    # Abuse protection for a public link: questions per visitor per hour, and per app per day (0 = off)
    rate_limit_per_hour: int = 0
    daily_question_limit: int = 0

    # Monitoring (optional)
    applicationinsights_connection_string: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
