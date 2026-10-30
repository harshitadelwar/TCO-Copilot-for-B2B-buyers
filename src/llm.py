"""Thin LLM client. Live mode calls the Anthropic API; mock mode makes no network calls at all."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    model: str = ""


class LLMClient:
    """Mode is 'live' when ANTHROPIC_API_KEY is set (and TCO_MOCK != 1), otherwise 'mock'.

    Model is read from TCO_MODEL (default below). Use a smaller/cheaper model for extraction by
    setting TCO_MODEL, e.g. TCO_MODEL=claude-haiku-5-5.
    """
    DEFAULT_MODEL = "claude-sonnet-5-5"

    def __init__(self, api_key: str | None = None, model: str | None = None, mock: bool | None = None):
        self.model = model or os.getenv("TCO_MODEL", self.DEFAULT_MODEL)
        self._key = api_key or os.getenv("ANTHROPIC_API_KEY")
        forced = os.getenv("TCO_MOCK") == "1"
        self.mock = mock if mock is not None else (forced or not self._key)
        self._client = None

    @property
    def mode(self) -> str:
        return "mock" if self.mock else "live"

    def complete(self, system: str, messages: list[dict], max_tokens: int = 1500,
                 temperature: float = 0.0) -> LLMResult:
        if self.mock:
            raise LLMError("LLM call attempted in mock mode. Set ANTHROPIC_API_KEY to run live.")
        try:
            import anthropic
        except ImportError as e:  # pragma: no cover
            raise LLMError("The 'anthropic' package is not installed. Run: pip install -r requirements.txt") from e
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=self._key)
        kwargs = dict(model=self.model, max_tokens=max_tokens, system=system, messages=messages)
        t0 = time.perf_counter()
        try:
            try:
                resp = self._client.messages.create(temperature=temperature, **kwargs)
            except anthropic.BadRequestError as e:
                if "temperature" not in str(e).lower():
                    raise
                resp = self._client.messages.create(**kwargs)   # some models fix their own sampling
        except anthropic.APIError as e:
            raise LLMError(f"Anthropic API error: {e}") from e
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return LLMResult(text=text, input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
                         latency_s=time.perf_counter() - t0, model=self.model)
