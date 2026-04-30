"""MCP client factory for the event_system mock tool servers.

The benchmark serves pre-recorded tool responses over **Streamable HTTP** MCP.
Each backend runs on its own port; URLs are configurable via ``AgentSettings``.
"""

from pydantic_ai.mcp import MCPServerStreamableHTTP

from src.common.config import AgentSettings, get_settings


def build_mcp_servers(settings: AgentSettings | None = None) -> list[MCPServerStreamableHTTP]:
    """Create streamable HTTP MCP server handles for all configured backends.

    These instances are passed to ``pydantic_ai.Agent`` as ``toolsets`` so the
    model can invoke kubectl, logs, metrics, and traces during RCA.

    Args:
        settings: Optional explicit settings; defaults to ``get_settings()``.

    Returns:
        Ordered list of MCP servers (kubectl, ES, Loki, Tempo, Prometheus).
    """
    cfg = settings or get_settings()
    return [
        MCPServerStreamableHTTP(cfg.kubectl_mcp_url),
        MCPServerStreamableHTTP(cfg.elasticsearch_mcp_url),
        MCPServerStreamableHTTP(cfg.loki_mcp_url),
        MCPServerStreamableHTTP(cfg.tempo_mcp_url),
        MCPServerStreamableHTTP(cfg.prometheus_mcp_url),
    ]
