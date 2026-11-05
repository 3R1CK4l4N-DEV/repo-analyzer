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


def get_provider(settings: Settings | None = None, provider: str | None = None, model: str | None = None) -> LLMProvider:
    settings = settings or get_settings()
    name = (provider or settings.llm_provider).lower()
    model = model or settings.llm_model
    if name == "ollama":
        from devanalyzer.llm.ollama import OllamaProvider

        return OllamaProvider(model=model, host=settings.ollama_host)
    if name == "openai":
        from devanalyzer.llm.openai import OpenAIProvider

        return OpenAIProvider(model=model, api_key=settings.openai_api_key)
    if name == "anthropic":
        from devanalyzer.llm.anthropic import AnthropicProvider

        return AnthropicProvider(model=model, api_key=settings.anthropic_api_key)
    raise ValueError(f"Unknown LLM provider: {name!r} (expected ollama|openai|anthropic)")


def get_verify_provider(settings: Settings | None = None) -> LLMProvider:
    """Judge model for rule verification; falls back to the main model when not configured."""
    settings = settings or get_settings()
    return get_provider(settings, provider=settings.verify_provider or settings.llm_provider, model=settings.verify_model or settings.llm_model)
