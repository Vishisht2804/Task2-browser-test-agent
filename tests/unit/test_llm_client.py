import json
import sys
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, SecretStr

from browser_agent.config import Settings
from browser_agent.llm.client import (
    LLMConfigurationError,
    LLMResponseError,
    OpenAICompatibleLLMClient,
    create_llm_client,
)


class TinyResponse(BaseModel):
    answer: str


class FakeCompletions:
    def __init__(self, content):
        self.content = content
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeAsyncOpenAI:
    instance = None

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.completions = FakeCompletions('{"answer":"validated"}')
        self.chat = SimpleNamespace(completions=self.completions)
        self.closed = False
        FakeAsyncOpenAI.instance = self

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_openai_compatible_adapter_sends_json_mode_and_validates_response(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))
    settings = Settings(
        _env_file=None,
        llm_api_key=SecretStr("test-key"),
        llm_model="test-model",
        llm_base_url="https://llm.example/v1",
    )
    client = OpenAICompatibleLLMClient(settings)

    result = await client.generate_structured(
        system_prompt="Return an answer.",
        user_prompt="Test prompt.",
        response_model=TinyResponse,
    )

    assert result.answer == "validated"
    assert FakeAsyncOpenAI.instance.init_kwargs["api_key"] == "test-key"
    assert FakeAsyncOpenAI.instance.init_kwargs["base_url"] == "https://llm.example/v1"
    assert FakeAsyncOpenAI.instance.completions.kwargs["response_format"] == {
        "type": "json_object"
    }
    assert "JSON Schema" in FakeAsyncOpenAI.instance.completions.kwargs["messages"][0]["content"]
    await client.aclose()
    assert FakeAsyncOpenAI.instance.closed is True


@pytest.mark.asyncio
async def test_adapter_rejects_invalid_structured_response(monkeypatch):
    class InvalidAsyncOpenAI(FakeAsyncOpenAI):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.completions.content = json.dumps({"unexpected": "field"})

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=InvalidAsyncOpenAI))
    client = OpenAICompatibleLLMClient(
        Settings(_env_file=None, llm_api_key=SecretStr("test-key"), llm_model="test-model")
    )

    with pytest.raises(LLMResponseError, match="did not match TinyResponse"):
        await client.generate_structured(
            system_prompt="Return an answer.",
            user_prompt="Test prompt.",
            response_model=TinyResponse,
        )



def test_adapter_requires_api_key():
    with pytest.raises(LLMConfigurationError, match="BA_LLM_API_KEY is required"):
        OpenAICompatibleLLMClient(Settings(_env_file=None))


def test_factory_rejects_unimplemented_provider_adapter():
    settings = Settings(
        _env_file=None,
        llm_provider="anthropic",
        llm_api_key=SecretStr("test-key"),
    )
    with pytest.raises(LLMConfigurationError, match="Supported providers"):
        create_llm_client(settings)


def test_adapter_requires_model_name():
    with pytest.raises(LLMConfigurationError, match="BA_LLM_MODEL is required"):
        OpenAICompatibleLLMClient(
            Settings(_env_file=None, llm_api_key=SecretStr("test-key"))
        )
