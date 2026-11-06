"""Provider-neutral LLM interface.

Concrete OpenAI/Anthropic/Gemini adapters will implement this protocol in Phase 3.
Browser and agent orchestration code will depend on this interface, not a vendor SDK.
"""

from typing import Protocol, TypeVar

from pydantic import BaseModel


ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class LLMClient(Protocol):
    """Minimal interface required by the planner and test interpreter."""

    async def generate_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_model: type[ResponseModel],
    ) -> ResponseModel:
        """Generate and validate a response against the supplied Pydantic model."""
        ...
