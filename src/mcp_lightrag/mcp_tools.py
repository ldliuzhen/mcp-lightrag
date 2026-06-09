"""
MCP tool definitions for LightRAG server.
"""

import functools
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Union, cast

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from .api_client import LightRAGApiClient
from .settings import get_settings
from .models import OperationResult, BatchResult
from .client.light_rag_server_api_client.models import QueryRequest, QueryRequestMode

logger = logging.getLogger(__name__)

QUERY_MODE_ALIASES = {
    "semantic": "naive",
    "vector": "naive",
    "keyword": "hybrid",
}

AUTO_QUERY_MODES = {"auto", "自动", "智能"}
DEFAULT_QUERY_LIMIT = 10
MAX_GENERATION_QUERY_LIMIT = 10
MAX_CONTEXT_QUERY_LIMIT = 30
AUTO_QUERY_MAX_ATTEMPTS = 2

DEFAULT_QUERY_USER_PROMPT = (
    "Please answer in the same language as the question. "
    "Use only the retrieved context. If the context contains relevant facts, "
    "synthesize them into a direct answer and cite the evidence implicitly in the response. "
    "Only say that there is insufficient information when the retrieved context is truly unrelated."
)

class AppContext:
    """Type-safe application context."""
    def __init__(self, client: LightRAGApiClient):
        self.api = client

@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manages the lifecycle of the API client."""
    # Re-fetch settings here to capture any environment variable overrides from CLI
    client = LightRAGApiClient(get_settings())
    try:
        yield AppContext(client)
    finally:
        await client.close()
        logger.info("LightRAG MCP service shut down")

# Initialize FastMCP with lifespan management
mcp = FastMCP("LightRAG-Server", lifespan=lifespan)

def format_output(func):
    """Decorator to standardize tool responses."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            result = await func(*args, **kwargs)
            return OperationResult.success(result).__dict__
        except Exception as e:
            logger.exception(f"Tool execution failed: {str(e)}")
            return OperationResult.failure(str(e)).__dict__
    return wrapper

async def get_api(ctx: Context) -> LightRAGApiClient:
    """Helper to extract the API client from MCP context."""
    if not ctx or not ctx.request_context or not ctx.request_context.lifespan_context:
        raise RuntimeError("Application context not initialized")
    return cast(AppContext, ctx.request_context.lifespan_context).api

def normalize_query_mode(search_mode: str) -> QueryRequestMode:
    """Normalize old MCP aliases to modes supported by current LightRAG APIs."""
    normalized = QUERY_MODE_ALIASES.get(search_mode.strip().lower(), search_mode.strip().lower())
    try:
        return QueryRequestMode(normalized)
    except ValueError as exc:
        valid_modes = ", ".join(mode.value for mode in QueryRequestMode)
        raise ValueError(f"Unsupported search_mode '{search_mode}'. Use one of: {valid_modes}") from exc

def plan_auto_query_modes(prompt: str) -> List[QueryRequestMode]:
    """Pick a small query-mode sequence from the question shape."""
    text = prompt.lower()
    graph_terms = ["关系", "关联", "实体", "图谱", "路径", "连接", "relationship", "entity", "graph"]
    broad_terms = ["总结", "概括", "有哪些", "影响", "趋势", "对比", "总体", "why", "compare", "summarize"]
    exact_terms = ["原文", "出处", "片段", "包含", "关键词", "quote", "source", "snippet"]

    if any(term in text for term in graph_terms):
        return [QueryRequestMode.LOCAL, QueryRequestMode.MIX, QueryRequestMode.GLOBAL, QueryRequestMode.NAIVE]
    if any(term in text for term in broad_terms):
        return [QueryRequestMode.GLOBAL, QueryRequestMode.MIX, QueryRequestMode.HYBRID, QueryRequestMode.NAIVE]
    if any(term in text for term in exact_terms):
        return [QueryRequestMode.NAIVE, QueryRequestMode.HYBRID, QueryRequestMode.MIX]
    return [QueryRequestMode.MIX, QueryRequestMode.LOCAL, QueryRequestMode.GLOBAL, QueryRequestMode.NAIVE]

def resolve_query_limit(limit: int, context_only: bool, prompt_only: bool) -> tuple[int, Dict[str, Any]]:
    """Keep LightRAG calls inside typical MCP client timeouts."""
    requested = max(1, int(limit))
    max_limit = MAX_CONTEXT_QUERY_LIMIT if context_only or prompt_only else MAX_GENERATION_QUERY_LIMIT
    applied = min(requested, max_limit)
    return applied, {
        "requested_limit": requested,
        "applied_limit": applied,
        "max_limit": max_limit,
        "limit_was_reduced": applied != requested,
        "reason": (
            "Generated RAG answers are capped to avoid MCP client request timeouts. "
            "Use context_only=true for larger raw-context retrieval."
            if applied != requested
            else None
        ),
    }

def build_query_request(
    prompt: str,
    mode: QueryRequestMode,
    limit: int,
    context_only: bool,
    prompt_only: bool,
    include_references: bool
) -> QueryRequest:
    token_scale = max(1, min(limit, MAX_GENERATION_QUERY_LIMIT))
    max_entity_tokens = 1024 + token_scale * 128
    max_relation_tokens = 1024 + token_scale * 128
    max_total_tokens = 4096 + token_scale * 256

    return QueryRequest(
        query=prompt,
        mode=mode,
        top_k=limit,
        chunk_top_k=limit,
        only_need_context=context_only,
        only_need_prompt=prompt_only,
        response_type="Multiple Paragraphs",
        max_entity_tokens=max_entity_tokens,
        max_relation_tokens=max_relation_tokens,
        max_total_tokens=max_total_tokens,
        user_prompt=DEFAULT_QUERY_USER_PROMPT,
        stream=False,
        include_references=include_references
    )

def serialize_api_result(result: Any) -> Any:
    if hasattr(result, "to_dict"):
        return result.to_dict()
    return result

def extract_response_text(result: Any) -> str:
    data = serialize_api_result(result)
    if isinstance(data, dict):
        value = data.get("response") or data.get("message") or data
        return str(value)
    return str(data)

def looks_insufficient_answer(text: str) -> bool:
    lowered = text.lower()
    markers = [
        "暂无足够信息",
        "没有足够信息",
        "未找到足够",
        "没有找到相关",
        "无法根据",
        "无法回答",
        "insufficient information",
        "not enough information",
        "no sufficient information",
        "do not have enough",
        "cannot answer",
        "i'm sorry",
    ]
    return any(marker in lowered for marker in markers)

# --- Search & Query Tools ---

@mcp.tool(name="query_knowledge_graph", description="Search the LightRAG knowledge base. The calling model may choose search_mode directly: use mix for general Q&A, local for entity/relationship questions, global for summaries/comparisons, hybrid for keyword+vector search, naive for source/snippet lookup, bypass to skip retrieval, or auto when unsure.")
@format_output
async def query_knowledge_graph(
    ctx: Context,
    prompt: str = Field(description="The question or search query to execute against the knowledge base"),
    search_mode: str = Field(
        description="Search strategy. Use 'auto' by default, or let the calling model choose: 'mix' for normal answers, 'local' for entity/relationship/path questions, 'global' for summaries/comparisons, 'hybrid' for keyword+semantic search, 'naive' for raw text/source lookup, 'bypass' to skip retrieval. Deprecated aliases: 'semantic' -> 'naive', 'keyword' -> 'hybrid'.",
        default="auto"
    ),
    limit: int = Field(description="Maximum number of result items/paragraphs to retrieve. Generated answers are capped at 10 to avoid MCP client timeouts; use context_only=True for larger raw-context retrieval.", default=DEFAULT_QUERY_LIMIT),
    context_only: bool = Field(description="If True, returns only the raw context data without LLM generation", default=False),
    prompt_only: bool = Field(description="If True, returns only the constructed LLM prompt without executing it to the LLM", default=False),
    include_references: bool = Field(description="If True, includes LightRAG reference metadata in the response. Keep False for faster MCP calls.", default=False),
) -> Any:
    """Execute a RAG query against the knowledge graph."""
    api = await get_api(ctx)
    effective_limit, limit_info = resolve_query_limit(limit, context_only, prompt_only)
    normalized_mode = search_mode.strip().lower()
    if normalized_mode not in AUTO_QUERY_MODES:
        params = build_query_request(
            prompt,
            normalize_query_mode(search_mode),
            effective_limit,
            context_only,
            prompt_only,
            include_references
        )
        result = await api.query(params)
        if limit_info["limit_was_reduced"]:
            return {
                "strategy": normalize_query_mode(search_mode).value,
                "limit": limit_info,
                "result": serialize_api_result(result),
            }
        return result

    attempts = []
    last_result = None
    for mode in plan_auto_query_modes(prompt)[:AUTO_QUERY_MAX_ATTEMPTS]:
        params = build_query_request(
            prompt,
            mode,
            effective_limit,
            context_only,
            prompt_only,
            include_references
        )
        result = await api.query(params)
        last_result = result
        response_text = extract_response_text(result)
        insufficient = looks_insufficient_answer(response_text)
        attempts.append({
            "mode": mode.value,
            "insufficient": insufficient,
            "preview": response_text[:300],
        })

        if context_only or prompt_only or not insufficient:
            return {
                "selected_mode": mode.value,
                "strategy": "auto",
                "limit": limit_info,
                "attempts": attempts,
                "result": serialize_api_result(result),
            }

    return {
        "selected_mode": attempts[-1]["mode"] if attempts else None,
        "strategy": "auto",
        "limit": limit_info,
        "attempts": attempts,
        "result": serialize_api_result(last_result),
    }

# --- Document Management Tools ---

@mcp.tool(name="ingest_text", description="Index raw text content directly into the knowledge graph. Useful for small snippets or dynamic data.")
@format_output
async def ingest_text(
    ctx: Context,
    content: Union[str, List[str]] = Field(description="The text content (string or list of strings) to be indexed")
) -> Any:
    api = await get_api(ctx)
    return await api.add_text(content)

@mcp.tool(name="ingest_file", description="Index a specific local file from the file system. The file must be accessible by the running server.")
@format_output
async def ingest_file(
    ctx: Context,
    file_path: str = Field(description="Absolute path to the local file to be indexed")
) -> Any:
    api = await get_api(ctx)
    return await api.index_file(file_path)

@mcp.tool(name="upload_and_index", description="Upload a file to the LightRAG server's input directory and trigger indexing. Handles file transfer if the server is remote.")
@format_output
async def upload_and_index(
    ctx: Context,
    file_path: str = Field(description="Local path to the file to upload and index")
) -> Any:
    api = await get_api(ctx)
    return await api.upload_file(file_path)

@mcp.tool(name="upsert_document", description="Intelligently upload a document: if it doesn't exist, creates it; if it exists and is identical (same content length), skips upload; if it exists but was modified, deletes the old version and re-uploads.")
@format_output
async def upsert_document(
    ctx: Context,
    file_path: str = Field(description="Local path to the document file to upsert")
) -> Any:
    """
    Smart document upload that handles three scenarios:
    - NEW: Document doesn't exist → uploads it
    - IDENTICAL: Document exists with same content → skips (returns success)
    - MODIFIED: Document exists but content changed → deletes old, uploads new
    """
    api = await get_api(ctx)
    return await api.upsert_document(file_path)


@mcp.tool(name="ingest_batch", description="Recursively index all files in a directory that match specific patterns.")
@format_output
async def ingest_batch(
    ctx: Context,
    directory_path: str = Field(description="Absolute path to the directory to scan"),
    recursive: bool = Field(description="If True, scans subdirectories recursively", default=False),
    max_depth: int = Field(description="Maximum depth for recursive scanning", default=1),
    include_patterns: List[str] = Field(description="List of glob patterns for files to include (e.g. ['*.txt', '*.md'])", default_factory=list),
    ignore_patterns: List[str] = Field(description="List of glob patterns for files to exclude", default_factory=list)
) -> Any:
    api = await get_api(ctx)
    return await api.ingest_batch(
        directory=directory_path,
        recursive=recursive,
        depth=max_depth,
        include_only=include_patterns,
        ignore_files=ignore_patterns
    )

@mcp.tool(name="list_all_docs", description="List ALL documents currently in the system. WARNING: Can be slow if there are many documents. Use get_latest_documents for better performance.")
@format_output
async def list_all_docs(ctx: Context) -> Any:
    api = await get_api(ctx)
    return await api.get_all_documents()

@mcp.tool(name="find_document", description="Check if a document exists by its filename or path. Returns a dictionary with detailed status: 'id', 'status' (processed/failed/pending), 'created_at', 'updated_at', 'content_length', 'chunks_count', and 'error_msg' if any.")
@format_output
async def find_document(
    ctx: Context,
    filename: str = Field(description="The name or path of the document file to find (e.g., 'report.pdf')")
) -> Any:
    api = await get_api(ctx)
    doc = await api.find_document_by_file_name(filename)
    if doc and hasattr(doc, "to_dict"):
        return doc.to_dict()
    return doc

@mcp.tool(name="get_latest_documents", description="Get a paginated list of the most recently updated documents. Useful for monitoring ingestion progress.")
@format_output
async def get_latest_documents(
    ctx: Context,
    limit: int = Field(description="Number of documents to retrieve (10-100)", default=10),
    status: str = Field(description="Optional filter by status (e.g. 'processed', 'failed', 'pending')", default=None)
) -> Any:
    api = await get_api(ctx)
    # Ensure limit is within API bounds (10-200)
    limit = max(10, min(limit, 100))
    result = await api.get_documents_paginated(page=1, page_size=limit, sort_field="updated_at", sort_direction="desc", status_filter=status)
    if result and hasattr(result, "to_dict"):
        return result.to_dict()
    return result

@mcp.tool(name="check_indexing_status", description="Check the current status of the document processing pipeline (idle or busy).")
@format_output
async def check_indexing_status(ctx: Context) -> Any:
    api = await get_api(ctx)
    return await api.get_pipeline_status()

# --- Graph Schema & Health ---

@mcp.tool(name="get_graph_metadata", description="Retrieve schema information about the knowledge graph, including available node labels and relationship types.")
@format_output
async def get_graph_metadata(ctx: Context) -> Any:
    api = await get_api(ctx)
    return await api.get_labels()

@mcp.tool(name="verify_server_health", description="Check if the LightRAG server is reachable and healthy.")
@format_output
async def verify_server_health(ctx: Context) -> Any:
    api = await get_api(ctx)
    return await api.check_health()

@mcp.tool(name="diagnose_lightrag_connection", description="Diagnose LightRAG connectivity, auth, and protected API availability using the current MCP configuration.")
@format_output
async def diagnose_lightrag_connection(ctx: Context) -> Any:
    api = await get_api(ctx)
    return await api.diagnose_connection()

# --- Entity & Relationship Management ---

@mcp.tool(name="create_entities", description="Manually insert specific entities into the knowledge graph.")
@format_output
async def create_entities(
    ctx: Context,
    entities: List[Dict[str, Any]] = Field(description="List of entity dictionaries. Each must contain 'name'. Optional fields include 'type'/'entity_type', 'description', and 'source_id'.")
) -> Any:
    api = await get_api(ctx)
    results = []
    for e in entities:
        try:
            name = e.get("name") or e.get("entity_name")
            if not name:
                raise ValueError("entity must include 'name'")

            extra_data = {
                k: v for k, v in e.items()
                if k not in {"name", "entity_name", "type", "entity_type", "description", "source_id"}
            }
            res = await api.create_entity(
                name=str(name),
                type=str(e.get("entity_type", e.get("type"))) if e.get("entity_type", e.get("type")) is not None else None,
                description=str(e["description"]) if e.get("description") is not None else None,
                source_id=str(e["source_id"]) if e.get("source_id") is not None else None,
                extra_data=extra_data
            )
            results.append({"name": name, "status": "ok", "data": res})
        except Exception as err:
            results.append({"name": e.get('name', 'unknown'), "status": "fail", "error": str(err)})
    
    return BatchResult(
        total=len(entities),
        successful=sum(1 for r in results if r['status'] == 'ok'),
        failed=sum(1 for r in results if r['status'] == 'fail'),
        results=results
    )

@mcp.tool(name="remove_entities", description="Delete one or more specific entities from the knowledge graph by name.")
@format_output
async def remove_entities(
    ctx: Context,
    names: List[str] = Field(description="List of entity names to delete")
) -> Any:
    api = await get_api(ctx)
    results = []
    for name in names:
        try:
            res = await api.delete_entity(name)
            results.append({"name": name, "status": "ok", "data": res})
        except Exception as err:
            results.append({"name": name, "status": "fail", "error": str(err)})
    return results

@mcp.tool(name="purge_by_document", description="Remove all entities and relationships associated with specific document IDs from the graph.")
@format_output
async def purge_by_document(
    ctx: Context,
    doc_ids: List[str] = Field(description="List of document IDs (e.g., from find_document) to prune from the graph")
) -> Any:
    api = await get_api(ctx)
    results = []
    for doc_id in doc_ids:
        try:
            res = await api.delete_by_doc(doc_id)
            results.append({"id": doc_id, "status": "ok", "data": res})
        except Exception as err:
            results.append({"id": doc_id, "status": "fail", "error": str(err)})
    return results

@mcp.tool(name="modify_entities", description="Update the properties (type, description, source_id) of existing entities.")
@format_output
async def modify_entities(
    ctx: Context,
    entities: List[Dict[str, Any]] = Field(description="List of dictionaries with updated entity fields. Must include 'name'. Optional: 'type', 'description', 'source_id'")
) -> Any:
    api = await get_api(ctx)
    results = []
    for e in entities:
        try:
            name = e.get("name") or e.get("entity_name")
            if not name:
                raise ValueError("entity must include 'name'")

            extra_data = {
                k: v for k, v in e.items()
                if k not in {
                    "name", "entity_name", "type", "entity_type", "description",
                    "source_id", "allow_rename", "allow_merge"
                }
            }
            res = await api.edit_entity(
                name=str(name),
                type=str(e.get("entity_type", e.get("type"))) if e.get("entity_type", e.get("type")) is not None else None,
                description=str(e["description"]) if e.get("description") is not None else None,
                source_id=str(e["source_id"]) if e.get("source_id") is not None else None,
                extra_data=extra_data,
                allow_rename=bool(e.get("allow_rename", False)),
                allow_merge=bool(e.get("allow_merge", False))
            )
            results.append({"name": name, "status": "ok", "data": res})
        except Exception as err:
            results.append({"name": e.get("name", "unknown"), "status": "fail", "error": str(err)})
    return results

@mcp.tool(name="connect_entities", description="Define or update relationships between entities, including edge weights and descriptions.")
@format_output
async def connect_entities(
    ctx: Context,
    relations: List[Dict[str, Any]] = Field(description="List of relationship definitions. Required: 'source', 'target'. Optional: 'description', 'keywords', 'weight', 'type', 'edit_mode'")
) -> Any:
    api = await get_api(ctx)
    results = []
    for r in relations:
        try:
            res = await api.manage_relation(
                source=str(r['source']),
                target=str(r['target']),
                description=str(r.get('description', '')),
                keywords=str(r.get('keywords', r.get('type', ''))),
                relation_type=r.get('type'),
                source_id=r.get('source_id'),
                weight=r.get('weight'),
                is_edit=bool(r.get('edit_mode', False))
            )
            results.append({"rel": f"{r['source']}->{r['target']}", "status": "ok", "data": res})
        except Exception as err:
            results.append({"rel": f"{r.get('source')}->{r.get('target')}", "status": "fail", "error": str(err)})
    return results

@mcp.tool(name="unify_entities", description="Merge multiple source entities into a single target entity to resolve duplicates or synonyms.")
@format_output
async def unify_entities(
    ctx: Context,
    sources: List[str] = Field(description="List of entity names to be merged (will be removed)"),
    target: str = Field(description="Name of the resolving entity (will function as the canonical entity)"),
    strategies: Dict[str, str] = Field(description="Strategy per field (e.g. {'description': 'concatenate'}). Options: keep_first, keep_last, concatenate", default_factory=dict)
) -> Any:
    api = await get_api(ctx)
    return await api.merge_entities(sources, target, strategies)
