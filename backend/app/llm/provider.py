"""One interface for every LLM. New local servers (Ollama, llama.cpp, vLLM, LM Studio, Bionic) are
OpenAI-compatible, so adding one is a row in the models table — no code."""
import asyncio
import json
import re
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import LLMModel
from app.store import decrypt_secret


class LLMError(RuntimeError):
    pass


@dataclass
class ModelConfig:
    id: int | None
    display_name: str
    provider_type: str
    base_url: str
    model_name: str
    api_key: str | None
    context_length: int
    temperature: float
    max_output_tokens: int

    @classmethod
    def from_row(cls, m: LLMModel) -> "ModelConfig":
        return cls(id=m.id, display_name=m.display_name, provider_type=m.provider_type, base_url=m.base_url,
                   model_name=m.model_name, api_key=decrypt_secret(m.api_key_enc), context_length=m.context_length,
                   temperature=m.temperature, max_output_tokens=m.max_output_tokens)


class LLMProvider(Protocol):
    def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]: ...

    async def health(self) -> tuple[bool, str]: ...


_semaphore: asyncio.Semaphore | None = None


def _sem() -> asyncio.Semaphore:
    # The Qwen endpoint is shared with other apps (AiTrading): cap our concurrent requests.
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_settings().llm_max_concurrency)
    return _semaphore


_THINK = re.compile(r"<think>.*?</think>", re.S)


class OpenAICompatibleProvider:
    def __init__(self, cfg: ModelConfig):
        self.cfg = cfg
        self.base = cfg.base_url.rstrip("/")

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}

    async def stream_chat(self, messages: list[dict]) -> AsyncIterator[str]:
        body = {"model": self.cfg.model_name, "messages": messages, "stream": True,
                "temperature": self.cfg.temperature, "max_tokens": self.cfg.max_output_tokens}
        timeout = httpx.Timeout(get_settings().llm_timeout_seconds, connect=10)
        in_think = False
        async with _sem():
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    async with client.stream("POST", f"{self.base}/chat/completions", json=body,
                                             headers=self._headers()) as r:
                        if r.status_code >= 400:
                            detail = (await r.aread()).decode(errors="replace")[:300]
                            raise LLMError(f"Model server returned {r.status_code}: {detail}")
                        async for line in r.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if data == "[DONE]":
                                break
                            try:
                                delta = json.loads(data)["choices"][0].get("delta", {}).get("content") or ""
                            except (ValueError, KeyError, IndexError):
                                continue
                            # Hide reasoning blocks from models that emit <think>…</think>.
                            if "<think>" in delta:
                                in_think, delta = True, delta.split("<think>")[0]
                            if in_think:
                                if "</think>" in delta:
                                    in_think, delta = False, delta.split("</think>", 1)[1]
                                else:
                                    continue
                            if delta:
                                yield delta
            except httpx.HTTPError as e:
                raise LLMError(f"Could not reach the model server at {self.base} ({type(e).__name__}).") from e

    async def health(self) -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=4) as client:
                r = await client.get(f"{self.base}/models", headers=self._headers())
            return r.status_code < 400, f"HTTP {r.status_code}"
        except httpx.HTTPError as e:
            return False, type(e).__name__


def provider_for(cfg: ModelConfig) -> LLMProvider:
    if cfg.provider_type == "openai_compatible":
        return OpenAICompatibleProvider(cfg)
    raise LLMError(f"Unknown provider type: {cfg.provider_type}")


def resolve_model(db: Session, model_id: int | None) -> ModelConfig:
    q = select(LLMModel).where(LLMModel.enabled)
    m = db.scalar(q.where(LLMModel.id == model_id)) if model_id else None
    m = m or db.scalar(q.where(LLMModel.is_default)) or db.scalar(q.order_by(LLMModel.id))
    if m is None:
        raise LLMError("No language model is configured. Add one under Admin → Models.")
    return ModelConfig.from_row(m)


def strip_think(text: str) -> str:
    return _THINK.sub("", text)
