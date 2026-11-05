from __future__ import annotations

from ollama import Client

from devanalyzer.llm.provider import LLMProvider


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, model: str, host: str):
        super().__init__(model)
        self.client = Client(host=host)

    def generate(self, prompt: str, system: str | None = None, temperature: float = 0.0) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        answer = self.client.chat(model=self.model, messages=messages, options={"temperature": temperature})
        return answer["message"]["content"]
