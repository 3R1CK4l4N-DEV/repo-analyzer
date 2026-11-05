from __future__ import annotations

from devanalyzer.llm.provider import LLMProvider


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str, api_key: str | None):
        super().__init__(model)
        if not api_key:
            raise ValueError("DEVANALYZER_OPENAI_API_KEY is required for the openai provider")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError("Install the optional dependency: pip install 'devanalyzer[openai]'") from exc
        self.client = OpenAI(api_key=api_key)

    def generate(self, prompt: str, system: str | None = None, temperature: float = 0.0) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        response = self.client.chat.completions.create(model=self.model, messages=messages, temperature=temperature)
        return response.choices[0].message.content or ""
