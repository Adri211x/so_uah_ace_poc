#!/usr/bin/env bash
set -euo pipefail

echo "Stopping mock MCP server..."
pkill -f "event_system.mock_mcp_server" 2>/dev/null && echo "Stopped." || echo "Not running."
