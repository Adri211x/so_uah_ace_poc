"""LLM model setup using the corporate LiteLLM OpenAI-compatible proxy.

pydantic-ai uses ``OpenAIModel`` + ``OpenAIProvider`` so the same code path works
for OpenAI and for proxies that expose the same HTTP API (LiteLLM here).
"""

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from src.common.config import AgentSettings, get_settings


def build_model(
    settings: AgentSettings | None = None,
    model_name: str | None = None,
) -> OpenAIChatModel:
    """Build the OpenAI-compatible model backed by LiteLLM.

    Args:
        settings: Optional explicit settings; defaults to ``get_settings()``.
        model_name: Optional override for the model name. When ``None``, uses
            ``settings.llm_model_name``. The judge passes ``judge_model_name``
            so it can run on a different model than the agent under test.

    Returns:
        Model instance wired to ``litellm_base_url`` and ``litellm_api_key``.
    """
    cfg = settings or get_settings()
    provider = OpenAIProvider(base_url=cfg.litellm_base_url, api_key=cfg.litellm_api_key)
    return OpenAIChatModel(model_name or cfg.llm_model_name, provider=provider)
