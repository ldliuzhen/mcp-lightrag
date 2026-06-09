"""
Data models and type definitions for LightRAG MCP server.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass(frozen=True)
class ServerSettings:
    """Settings for the LightRAG API connection."""
    host: str = "localhost"
    port: int = 9621
    url: str = ""
    api_key: str = ""
    api_key_header: str = "X-API-Key"
    api_key_prefix: str = ""
    username: str = ""
    password: str = ""
    
    @property
    def base_url(self) -> str:
        if self.url:
            return self.url.rstrip("/")

        host = self.host.strip().rstrip("/")
        if host.startswith(("http://", "https://")):
            return host

        return f"http://{host}:{self.port}"

@dataclass
class QueryParams:
    """Parameters for document queries."""
    query: str
    mode: str = "mix"
    top_k: int = 10
    chunk_top_k: int = 10
    only_need_context: bool = False
    only_need_prompt: bool = False
    response_type: str = "Multiple Paragraphs"
    max_entity_tokens: int = 4096
    max_relation_tokens: int = 4096
    max_total_tokens: int = 6656
    hl_keywords: List[str] = field(default_factory=list)
    ll_keywords: List[str] = field(default_factory=list)
    conversation_history: List[Dict[str, Any]] = field(default_factory=list)
    user_prompt: Optional[str] = None
    enable_rerank: Optional[bool] = None
    include_references: bool = True
    include_chunk_content: bool = False
    stream: bool = False

@dataclass
class OperationResult:
    """Standardized response for API operations."""
    status: str
    response: Optional[Any] = None
    error: Optional[str] = None
    
    @classmethod
    def success(cls, data: Any) -> "OperationResult":
        return cls(status="success", response=data)
    
    @classmethod
    def failure(cls, error_msg: str) -> "OperationResult":
        return cls(status="error", error=error_msg)

@dataclass
class BatchResult:
    """Statistics for batch operations."""
    total: int
    successful: int
    failed: int
    results: List[Dict[str, Any]]
