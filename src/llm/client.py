"""Cliente LLM compatible con la API de chat completions de OpenAI.

Solo se usa para redactar. El payload ya viene calculado y validado por el
motor determinista; cualquier fallo del proveedor se resuelve publicando la
version determinista.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from src.config.settings import Settings

DEFAULT_TIMEOUT_SECONDS = 45.0


class LLMError(RuntimeError):
    """Fallo de red, HTTP o de formato del proveedor."""


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    max_output_tokens: int = 700
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    input_usd_per_1k: float = 0.00015
    output_usd_per_1k: float = 0.0006
    json_mode: bool = True

    @classmethod
    def from_settings(cls, settings: Settings) -> LLMConfig:
        return cls(
            base_url=settings.llm_base_url.rstrip("/"),
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            max_output_tokens=settings.llm_max_output_tokens,
            input_usd_per_1k=settings.llm_input_usd_per_1k,
            output_usd_per_1k=settings.llm_output_usd_per_1k,
            json_mode=settings.llm_json_mode,
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1000 * self.input_usd_per_1k
            + output_tokens / 1000 * self.output_usd_per_1k
        )

    def worst_case_cost(self, prompt_chars: int) -> float:
        """Cota superior simple antes de llamar: entrada + max_output_tokens."""
        estimated_input = prompt_chars // 4 + 50
        return self.estimate_cost(estimated_input, self.max_output_tokens)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


class ChatClient:
    """Cliente minimo para `/chat/completions` (OpenAI, OpenRouter, etc.)."""

    def __init__(
        self,
        config: LLMConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    def chat_json(self, messages: list[dict[str, str]]) -> LLMResponse:
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.config.max_output_tokens,
        }
        if self.config.json_mode:
            payload["response_format"] = {"type": "json_object"}

        url = f"{self.config.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        try:
            with httpx.Client(
                timeout=self.config.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMError(f"fallo del proveedor LLM: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("respuesta sin choices[0].message.content") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("respuesta vacia del proveedor")

        usage = data.get("usage") or {}
        return LLMResponse(
            text=content,
            model=str(data.get("model") or self.config.model),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
        )
