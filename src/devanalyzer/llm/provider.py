from __future__ import annotations

from abc import ABC, abstractmethod

from devanalyzer.config import Settings, get_settings


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, model: str):
        self.model = model

    @abstractmethod
    def generate(self, prompt: str, system: str | None = None, temperature: float = 0.0) -> str:
        """Return the model's text completion for the given prompt."""


def get_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    provider = settings.llm_provider.lower()
    if provider == "ollama":
        from devanalyzer.llm.ollama import OllamaProvider

        return OllamaProvider(model=settings.llm_model, host=settings.ollama_host)
    if provider == "openai":
        from devanalyzer.llm.openai import OpenAIProvider

        return OpenAIProvider(model=settings.llm_model, api_key=settings.openai_api_key)
    if provider == "anthropic":
        from devanalyzer.llm.anthropic import AnthropicProvider

        return AnthropicProvider(model=settings.llm_model, api_key=settings.anthropic_api_key)
    raise ValueError(f"Unknown LLM provider: {settings.llm_provider!r} (expected ollama|openai|anthropic)")
