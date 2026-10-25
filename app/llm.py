"""Thin wrapper over Azure OpenAI: embeddings, chat, streaming chat and image description."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from dataclasses import dataclass, field

from .config import Settings


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens


@dataclass
class StreamResult:
    """Collects the token usage reported at the end of a streamed answer."""

    usage: Usage = field(default_factory=Usage)


IMAGE_PROMPT = (
    "You are indexing an image from the document '{title}' so people can later search for it. "
    "Describe it completely and factually: its type (diagram, chart, calendar, table, photo), "
    "every piece of readable text, every date, number, name and label, and what arrows or lines connect. "
    "For calendars, list every highlighted date with its label. For flowcharts, describe each step and "
    "decision in order. Do not add facts that are not visible."
)


class AzureLLM:
    def __init__(self, settings: Settings):
        from openai import AzureOpenAI

        if not settings.azure_openai_endpoint:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT is not set. Copy .env.example to .env and fill it in.")
        kwargs = {"azure_endpoint": settings.azure_openai_endpoint, "api_version": settings.azure_openai_api_version}
        if settings.azure_openai_api_key:
            kwargs["api_key"] = settings.azure_openai_api_key
        else:
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            kwargs["azure_ad_token_provider"] = get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )
        self.client = AzureOpenAI(**kwargs)
        self.settings = settings

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 16):
            resp = self.client.embeddings.create(
                model=self.settings.azure_openai_embedding_deployment,
                input=texts[i : i + 16],
                dimensions=self.settings.embedding_dimensions,
            )
            out.extend(d.embedding for d in resp.data)
        return out

    def chat(self, messages: list[dict], temperature: float = 0.0, max_tokens: int = 800) -> tuple[str, Usage]:
        resp = self.client.chat.completions.create(
            model=self.settings.azure_openai_chat_deployment,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        usage = Usage(resp.usage.prompt_tokens, resp.usage.completion_tokens) if resp.usage else Usage()
        return resp.choices[0].message.content or "", usage

    def stream(self, messages: list[dict], result: StreamResult, temperature: float = 0.1) -> Iterator[str]:
        stream = self.client.chat.completions.create(
            model=self.settings.azure_openai_chat_deployment,
            messages=messages,
            temperature=temperature,
            max_tokens=1000,
            stream=True,
            stream_options={"include_usage": True},
        )
        for event in stream:
            if event.usage:
                result.usage = Usage(event.usage.prompt_tokens, event.usage.completion_tokens)
            if event.choices and event.choices[0].delta and event.choices[0].delta.content:
                yield event.choices[0].delta.content

    def describe_image(self, image: bytes, mime: str, title: str) -> str:
        data_url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
        text, _ = self.chat(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": IMAGE_PROMPT.format(title=title)},
                        {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    ],
                }
            ],
            max_tokens=1200,
        )
        return text.strip()
