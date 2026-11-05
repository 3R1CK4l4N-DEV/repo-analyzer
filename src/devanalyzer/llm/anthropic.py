from __future__ import annotations

from devanalyzer.llm.provider import LLMProvider


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, model: str, api_key: str | None, max_tokens: int = 4096):
        super().__init__(model)
        if not api_key:
            raise ValueError("DEVANALYZER_ANTHROPIC_API_KEY is required for the anthropic provider")
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise ImportError("Install the optional dependency: pip install 'devanalyzer[anthropic]'") from exc
        self.client = Anthropic(api_key=api_key)
        self.max_tokens = max_tokens

    def generate(self, prompt: str, system: str | None = None, temperature: float = 0.0) -> str:
        kwargs = {"system": system} if system else {}
        response = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        return "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
