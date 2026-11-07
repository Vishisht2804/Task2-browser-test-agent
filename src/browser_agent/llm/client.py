"""Provider-neutral LLM protocol and an OpenAI-compatible async adapter."""

from __future__ import annotations

import json
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from browser_agent.config import Settings

ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class LLMClientError(RuntimeError):
    """Base error for LLM configuration, transport, and response failures."""


class LLMConfigurationError(LLMClientError):
    """Raised when the selected model provider is not configured correctly."""


class LLMResponseError(LLMClientError):
    """Raised when a provider response cannot be parsed into the requested schema."""


class LLMClient(Protocol):
    """Minimal interface required by the interpreter and planner."""

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        """Generate a response and validate it against a Pydantic model."""
        ...


class OpenAICompatibleLLMClient:
    """Async adapter for OpenAI and services exposing an OpenAI-compatible API.

    JSON mode is used for broad compatibility. The requested Pydantic JSON Schema is
    included in the system prompt, and the response is validated locally before use.
    """

    def __init__(self, settings: Settings) -> None:
        if settings.llm_api_key is None or not settings.llm_api_key.get_secret_value():
            raise LLMConfigurationError(
                "BA_LLM_API_KEY is required. Set it in your local .env file or environment."
            )
        if not settings.llm_model:
            raise LLMConfigurationError(
                "BA_LLM_MODEL is required. Set a model name supported by your provider."
            )
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMConfigurationError(
                'The OpenAI-compatible client is not installed. Run '
                '`python -m pip install -e ".[llm]"`.'
            ) from exc

        client_options: dict[str, object] = {
            "api_key": settings.llm_api_key.get_secret_value(),
            "timeout": settings.llm_timeout_seconds,
            "max_retries": 1,
        }
        if settings.llm_base_url:
            client_options["base_url"] = settings.llm_base_url
        self._client = AsyncOpenAI(**client_options)
        self.model = settings.llm_model

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        schema = response_model.model_json_schema()
        schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        complete_system_prompt = (
            f"{system_prompt.strip()}\n\n"
            "OUTPUT CONTRACT: Return exactly one JSON object. Do not include markdown "
            "fences or commentary. The object must conform to this JSON Schema:\n"
            f"{schema_text}"
        )
        try:
            completion = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": complete_system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            # Avoid echoing provider exception bodies, which can include request details.
            raise LLMClientError(
                f"LLM request failed ({type(exc).__name__}). Check the API key, model, "
                "base URL, network connectivity, and provider quota."
            ) from exc

        if not completion.choices:
            raise LLMResponseError("The LLM returned no response choices.")
        content = completion.choices[0].message.content
        if not content:
            raise LLMResponseError("The LLM returned an empty response.")
        try:
            payload = json.loads(content)
            return response_model.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise LLMResponseError(
                f"The LLM response did not match {response_model.__name__}. "
                "No browser action was executed."
            ) from exc

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()


def create_llm_client(settings: Settings) -> LLMClient:
    """Construct the configured LLM adapter without coupling agent code to the SDK."""
    if settings.llm_provider in {"openai", "openai-compatible"}:
        return OpenAICompatibleLLMClient(settings)
    raise LLMConfigurationError(
        f"Unsupported BA_LLM_PROVIDER {settings.llm_provider!r}. "
        "Supported providers: openai, openai-compatible."
    )
