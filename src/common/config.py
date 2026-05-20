"""Environment configuration for RCA agents.

Loads secrets and endpoints from a local ``.env`` file (see ``.env.example``)
and from process environment variables. Field names map to env vars in
uppercase with underscores (for example ``litellm_api_key`` -> ``LITELLM_API_KEY``).
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """Runtime settings shared by all RCA agents.

    Attributes:
        llm_model_name: OpenAI-compatible model name served by the LiteLLM proxy.
        judge_model_name: Model name used by the LLM-as-a-judge evaluator. Defaults
            to ``llm_model_name`` semantics (same default value); override to use
            a stronger model just for evaluation without changing the agent.
        litellm_api_key: API key for the corporate LiteLLM endpoint (required at runtime via env).
        litellm_base_url: OpenAI-compatible base URL, including ``/v1`` if required.
        langfuse_public_key: Langfuse public key; tracing is skipped if unset.
        langfuse_secret_key: Langfuse secret key; tracing is skipped if unset.
        langfuse_base_url: Langfuse server URL for OTLP export.
        kubectl_mcp_url: Streamable HTTP MCP URL for the mock kubectl server.
        elasticsearch_mcp_url: MCP URL for Elasticsearch tools.
        loki_mcp_url: MCP URL for Loki log queries.
        tempo_mcp_url: MCP URL for Tempo traces.
        prometheus_mcp_url: MCP URL for Prometheus metrics.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_model_name: str = "gpt-4o-mini"
    judge_model_name: str = "gpt-4o-mini"
    # Default satisfies static analysis; env ``LITELLM_API_KEY`` overrides at load time.
    # ``_validate_litellm_api_key`` enforces a non-empty value after env/.env are applied.
    litellm_api_key: str = Field(
        default="",
        description="LiteLLM API key (environment variable LITELLM_API_KEY).",
    )
    litellm_base_url: str = "https://litellm.doi.azure.datadope.co/v1"

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str = "https://langfuse.e2e.so.azure.datadope.co"

    # Default ports match data/event_system mock MCP layout (8090, 8092-8095).
    kubectl_mcp_url: str = "http://127.0.0.1:8090/mcp"
    elasticsearch_mcp_url: str = "http://127.0.0.1:8092/mcp"
    loki_mcp_url: str = "http://127.0.0.1:8093/mcp"
    tempo_mcp_url: str = "http://127.0.0.1:8094/mcp"
    prometheus_mcp_url: str = "http://127.0.0.1:8095/mcp"

    @field_validator("litellm_api_key")
    @classmethod
    def _validate_litellm_api_key(cls, value: str) -> str:
        """Reject missing keys after settings sources (env file, process env) are merged."""
        trimmed = value.strip()
        if not trimmed:
            msg = "LITELLM_API_KEY is required. Set it in .env or the process environment."
            raise ValueError(msg)
        return trimmed


@lru_cache
def get_settings() -> AgentSettings:
    """Return a process-wide cached ``AgentSettings`` instance.

    Caching avoids re-parsing ``.env`` and environment variables on every agent call.

    Returns:
        Parsed and validated settings.
    """
    return AgentSettings()
