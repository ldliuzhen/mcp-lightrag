"""
Settings management for the LightRAG MCP server.
"""

import os
from .models import ServerSettings

def get_settings() -> ServerSettings:
    """
    Retrieve settings from environment variables with defaults.
    """
    return ServerSettings(
        host=os.environ.get("LIGHTRAG_HOST", "localhost"),
        port=int(os.environ.get("LIGHTRAG_PORT", 9621)),
        api_key=os.environ.get("LIGHTRAG_API_KEY", ""),
        api_key_header=os.environ.get("LIGHTRAG_API_KEY_HEADER", "Authorization"),
        api_key_prefix=os.environ.get("LIGHTRAG_API_KEY_PREFIX", "Bearer")
    )

# Default configuration instance
DEFAULT_SETTINGS = get_settings()
