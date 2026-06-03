"""
API client for LightRAG with robust error handling and retries.
"""

import asyncio
import logging
import re
import functools
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, TypeVar, Union

import httpx
from .exceptions import (
    APIConnectionError, 
    APIResponseError, 
    ResourceNotFoundError
)
from .models import ServerSettings

# Import auto-generated client components
from .client.light_rag_server_api_client.client import AuthenticatedClient

# Default
from .client.light_rag_server_api_client.api.default.get_status_health_get import asyncio as async_get_health

# Documents
from .client.light_rag_server_api_client.api.documents.documents_documents_get import asyncio as async_get_documents
from .client.light_rag_server_api_client.api.documents.get_pipeline_status_documents_pipeline_status_get import asyncio as async_get_pipeline_status
from .client.light_rag_server_api_client.api.documents.insert_text_documents_text_post import asyncio as async_insert_document
from .client.light_rag_server_api_client.api.documents.insert_texts_documents_texts_post import asyncio as async_insert_texts
from .client.light_rag_server_api_client.api.documents.scan_for_new_documents_documents_scan_post import asyncio as async_scan_for_new_documents
from .client.light_rag_server_api_client.api.documents.upload_to_input_dir_documents_upload_post import asyncio as async_upload_document
from .client.light_rag_server_api_client.api.documents.delete_document_documents_delete_document_delete import asyncio as async_delete_by_doc_id
from .client.light_rag_server_api_client.api.documents.delete_entity_documents_delete_entity_delete import asyncio as async_delete_entity


# Graph
from .client.light_rag_server_api_client.api.graph.create_entity_graph_entity_create_post import asyncio as async_create_entity
from .client.light_rag_server_api_client.api.graph.create_relation_graph_relation_create_post import asyncio as async_create_relation
from .client.light_rag_server_api_client.api.graph.update_entity_graph_entity_edit_post import asyncio as async_edit_entity
from .client.light_rag_server_api_client.api.graph.update_relation_graph_relation_edit_post import asyncio as async_edit_relation
from .client.light_rag_server_api_client.api.graph.get_graph_labels_graph_label_list_get import asyncio as async_get_graph_labels
from .client.light_rag_server_api_client.api.graph.get_knowledge_graph_graphs_get import asyncio as async_get_knowledge_graph
from .client.light_rag_server_api_client.api.graph.merge_entities_graph_entities_merge_post import asyncio as async_merge_entities

# Query
from .client.light_rag_server_api_client.api.query.query_text_query_post import asyncio as async_query_document

from .client.light_rag_server_api_client.models import (
    BodyUploadToInputDirDocumentsUploadPost,
    InsertTextRequest,
    InsertTextsRequest,
    QueryRequest,
    EntityMergeRequest as MergeEntitiesRequest,
    EntityCreateRequest,
    EntityCreateRequestEntityData,
    EntityUpdateRequest,
    EntityUpdateRequestUpdatedData,
    RelationCreateRequest,
    RelationCreateRequestRelationData,
    RelationUpdateRequest,
    RelationUpdateRequestUpdatedData,
    DeleteDocRequest,
    DeleteEntityRequest,
    DocumentsRequest,
    DocumentsRequestSortField,
    DocumentsRequestSortDirection,
)

from .client.light_rag_server_api_client.api.documents.get_documents_paginated_documents_paginated_post import asyncio as async_get_documents_paginated
from .client.light_rag_server_api_client.types import File
from .client.light_rag_server_api_client.errors import UnexpectedStatus

logger = logging.getLogger(__name__)

T = TypeVar("T")
RECOVERABLE_SERVER_STATUSES = {500, 503}

def with_retry(max_retries: int = 3, base_delay: float = 1.0):
    """
    Decorator for async methods to implement exponential backoff retry logic.
    """
    def decorator(func: Callable[..., Awaitable[T]]):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except (httpx.ConnectError, httpx.TimeoutException, UnexpectedStatus, APIConnectionError, APIResponseError) as e:
                    last_exception = e
                    # Don't retry on certain status codes if it's an UnexpectedStatus
                    if isinstance(e, UnexpectedStatus) and e.status_code in [400, 401, 403, 404]:
                        raise
                    if isinstance(e, APIResponseError) and e.status_code in [400, 401, 403, 404, 422]:
                        raise
                    
                    delay = base_delay * (2 ** attempt)
                    logger.warning(
                        f"Attempt {attempt + 1} failed for {func.__name__}: {str(e)}. "
                        f"Retrying in {delay:.2f}s..."
                    )
                    await asyncio.sleep(delay)
            
            logger.error(f"All {max_retries} attempts failed for {func.__name__}")
            raise last_exception
        return wrapper
    return decorator

class LightRAGApiClient:
    """
    Client for interacting with the LightRAG API.
    """

    def __init__(self, settings: ServerSettings):
        """
        Initialize the client with provided settings.
        """
        self.settings = settings
        self._runtime_token = settings.api_key
        self._runtime_auth_header = settings.api_key_header
        self._runtime_auth_prefix = settings.api_key_prefix
        self._login_attempted = False
        # Use AuthenticatedClient only if api_key is provided, otherwise use Client
        # This avoids sending invalid 'Bearer ' header when auth is disabled
        if settings.api_key:
            self.client = self._new_authenticated_client(
                settings.api_key,
                settings.api_key_header,
                settings.api_key_prefix
            )
        else:
            from .client.light_rag_server_api_client.client import Client
            self.client = Client(
                base_url=settings.base_url,
                verify_ssl=False,
                raise_on_unexpected_status=True
            )
        logger.info(f"Connected to LightRAG API at {settings.base_url}")

    def _new_authenticated_client(self, token: str, header: str, prefix: str) -> AuthenticatedClient:
        return AuthenticatedClient(
            base_url=self.settings.base_url,
            token=token,
            prefix=prefix,
            auth_header_name=header,
            verify_ssl=False,
            raise_on_unexpected_status=True
        )

    async def close(self):
        """Clean up resources."""
        await self.client.get_async_httpx_client().aclose()
        logger.debug("API client connection closed")

    @with_retry()
    async def _execute_op(self, api_func, name: str, **kwargs) -> Any:
        """Helper to execute API operations with logging and retries."""
        try:
            logger.debug(f"Starting operation: {name}")
            if name != "health_check":
                await self._ensure_login_token()
            result = await api_func(client=self.client, **kwargs)
            self._raise_for_error_response(result, name)
            return result
        except APIResponseError:
            raise
        except UnexpectedStatus as e:
            details = e.content.decode("utf-8", errors="replace")
            logger.error(f"API Error ({name}): {e.status_code} - {details}")
            hint = ""
            if e.status_code in [401, 403]:
                hint = (
                    ". Authentication failed; check LIGHTRAG_API_KEY, "
                    "LIGHTRAG_API_KEY_HEADER and LIGHTRAG_API_KEY_PREFIX"
                )
            raise APIResponseError(
                f"API operation '{name}' failed{hint}",
                status_code=e.status_code,
                details=details
            )
        except httpx.RequestError as e:
            logger.error(f"Connection error during {name} at {self.settings.base_url}: {str(e)}")
            raise APIConnectionError(
                f"Failed to connect for '{name}' at {self.settings.base_url}: {e}"
            ) from e
        except Exception:
            logger.exception(f"Unexpected error during {name}")
            raise

    @staticmethod
    def _raise_for_error_response(result: Any, name: str) -> None:
        """Raise for generated error models returned on documented error statuses."""
        class_name = result.__class__.__name__ if result is not None else ""
        is_error_model = class_name == "HTTPValidationError" or re.search(r"Response[45]\d\d$", class_name)
        if not is_error_model:
            return

        status_match = re.search(r"Response([45]\d\d)$", class_name)
        status_code = int(status_match.group(1)) if status_match else 422
        details = result.to_dict() if hasattr(result, "to_dict") else result
        raise APIResponseError(
            f"API operation '{name}' returned an error response",
            status_code=status_code,
            details=str(details)
        )

    # --- Document Operations ---

    async def query(self, params: 'QueryRequest') -> Any:
        """Perform a knowledge graph query."""
        return await self._execute_op(async_query_document, "query", body=params)

    async def add_text(self, text: Union[str, List[str]]) -> Any:
        """Insert text content into the graph."""
        if isinstance(text, str):
            request = InsertTextRequest(text=text)
            return await self._execute_op(async_insert_document, "insert_text", body=request)
        else:
            request = InsertTextsRequest(texts=text)
            return await self._execute_op(async_insert_texts, "insert_texts", body=request)

    async def upload_file(self, file_path: Union[str, Path]) -> Any:
        """Upload a file to the inputs directory for processing."""
        path = Path(file_path)
        if not path.exists():
            raise ResourceNotFoundError(f"File not found: {file_path}")
            
        with open(path, "rb") as f:
            request = BodyUploadToInputDirDocumentsUploadPost(
                file=File(payload=f, file_name=path.name)
            )
            return await self._execute_op(async_upload_document, f"upload_{path.name}", body=request)

    async def index_file(self, file_path: Union[str, Path]) -> Any:
        """Directly index a local file.
           Deprecated: Use upload_file instead as direct indexing is no longer supported.
        """
        # Fallback to upload_file since insert_file is removed
        return await self.upload_file(file_path)

    async def get_all_documents(self) -> Any:
        """Retrieve list of all indexed documents."""
        # Use paginated API to fetch all documents if possible, or fallback to legacy endpoint
        # The legacy endpoint is deprecated and limited to 1000 docs
        return await self._execute_op(async_get_documents, "list_documents")

    async def get_documents_paginated(
        self, 
        page: int = 1, 
        page_size: int = 50,
        sort_field: str = "updated_at",
        sort_direction: str = "desc",
        status_filter: Optional[str] = None
    ) -> Any:
        """Retrieve documents with pagination."""
        request = DocumentsRequest(
            page=page,
            page_size=page_size,
            sort_field=DocumentsRequestSortField(sort_field),
            sort_direction=DocumentsRequestSortDirection(sort_direction),
            status_filter=status_filter or None
        )
        return await self._execute_op(async_get_documents_paginated, "list_documents_paginated", body=request)

    async def find_document_by_file_name(self, file_name: str) -> Optional[Any]:
        """
        Find a document by its file name or path.
        Returns the document DocStatusResponse object if found, None otherwise.
        
        This method iterates through paginated results to find the document.
        """
        page = 1
        page_size = 100
        
        while True:
            response = await self.get_documents_paginated(page=page, page_size=page_size)
            
            # Handle response structure
            if not response:
                break
                
            docs = getattr(response, "documents", [])
            
            # Access pagination object properties correctly
            pagination = getattr(response, "pagination", None)
            total_count = getattr(pagination, "total_count", 0) if pagination else 0
            
            # Check current page
            for doc in docs:
                doc_path = getattr(doc, "file_path", "") or ""
                doc_name = Path(doc_path).name
                
                # Check for exact match on full path or filename
                if file_name == doc_path or file_name == doc_name:
                    return doc
                
                # Only log if check fails
                # logger.debug(f"Checking doc: {doc_path} ({doc_name}) vs {file_name}")
            
            # Check if we need to fetch next page
            if (page * page_size) >= total_count or not docs:
                break
                
            page += 1
            
        return None

    async def get_pipeline_status(self) -> Any:
        """Check the status of the indexing pipeline."""
        try:
            return await self._execute_op(async_get_pipeline_status, "pipeline_status")
        except APIResponseError as e:
            if e.status_code not in RECOVERABLE_SERVER_STATUSES:
                raise

            try:
                health = await self.check_health()
                health_data = health.to_dict() if hasattr(health, "to_dict") else health
                busy = health_data.get("pipeline_busy") if isinstance(health_data, dict) else None
            except Exception as health_error:
                health_data = None
                busy = None
                health_error_text = str(health_error)
            else:
                health_error_text = None

            return {
                "status": "unavailable",
                "source": "health_fallback",
                "busy": busy,
                "message": (
                    f"Pipeline status endpoint returned HTTP {e.status_code}; "
                    "using /health fallback when available."
                ),
                "error": str(e),
                "health_error": health_error_text,
                "health": health_data,
            }

    async def scan_inputs(self) -> Any:
        """Trigger a scan for new files in the inputs directory."""
        return await self._execute_op(async_scan_for_new_documents, "scan_inputs")

    async def ingest_batch(
        self,
        directory: Union[str, Path],
        recursive: bool = False,
        depth: int = 1,
        include_only: List[str] = None,
        ignore_files: List[str] = None,
        ignore_dirs: List[str] = None
    ) -> Dict[str, Any]:
        """Index a collection of files from a directory."""
        dir_path = Path(directory)
        if not dir_path.exists() or not dir_path.is_dir():
            raise ResourceNotFoundError(f"Directory not found: {directory}")

        include_re = [re.compile(p) for p in (include_only or [])]
        ignore_file_re = [re.compile(p) for p in (ignore_files or [])]
        ignore_dir_re = [re.compile(p) for p in (ignore_dirs or [])]

        def should_include(p: Path) -> bool:
            if include_re:
                return any(r.search(p.name) for r in include_re)
            if ignore_file_re:
                return not any(r.search(p.name) for r in ignore_file_re)
            return True

        def should_ignore_dir(p: Path) -> bool:
            return any(r.search(p.name) for r in ignore_dir_re)

        files_to_process = []

        def collect(curr_dir: Path, curr_depth: int):
            for item in curr_dir.iterdir():
                if item.is_dir() and recursive and curr_depth < depth:
                    if not should_ignore_dir(item):
                        collect(item, curr_depth + 1)
                elif item.is_file():
                    if should_include(item):
                        files_to_process.append(item)

        collect(dir_path, 0)
        logger.info(f"Found {len(files_to_process)} files in {directory}")

        results = []
        for f in files_to_process:
            try:
                await self.index_file(f)
                results.append({"file": str(f), "status": "ok"})
            except Exception as e:
                results.append({"file": str(f), "status": "fail", "error": str(e)})

        return {
            "total": len(files_to_process),
            "successful": sum(1 for r in results if r['status'] == 'ok'),
            "failed": sum(1 for r in results if r['status'] == 'fail'),
            "details": results
        }

    # --- Graph Operations ---

    async def get_labels(self) -> Any:
        """Get labels from the knowledge graph."""
        try:
            return await self._execute_op(async_get_graph_labels, "get_labels")
        except APIResponseError as e:
            if e.status_code not in RECOVERABLE_SERVER_STATUSES:
                raise

            try:
                graph = await self._execute_op(
                    async_get_knowledge_graph,
                    "get_graph_fallback",
                    label="*",
                    max_depth=3,
                    max_nodes=1000
                )
            except Exception as fallback_error:
                return {
                    "source": "graph_unavailable",
                    "message": (
                        f"Graph label endpoint returned HTTP {e.status_code}; "
                        "/graphs fallback also failed."
                    ),
                    "labels": [],
                    "error": str(e),
                    "fallback_error": str(fallback_error),
                }

            return {
                "source": "graphs_fallback",
                "message": (
                    f"Graph label endpoint returned HTTP {e.status_code}; "
                    "returned /graphs data instead."
                ),
                "labels": self._extract_graph_labels(graph),
                "graph": graph,
            }

    @staticmethod
    def _extract_graph_labels(graph: Any) -> List[str]:
        """Best-effort label extraction from varying /graphs response shapes."""
        labels = set()
        if not isinstance(graph, dict):
            return []

        nodes = graph.get("nodes")
        if nodes is None and isinstance(graph.get("data"), dict):
            nodes = graph["data"].get("nodes")

        if isinstance(nodes, list):
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                value = node.get("label") or node.get("entity_type") or node.get("type")
                if isinstance(value, str):
                    labels.add(value)
                values = node.get("labels")
                if isinstance(values, list):
                    labels.update(str(item) for item in values)

        return sorted(labels)

    async def create_entity(
        self,
        name: str,
        type: Optional[str] = None,
        description: Optional[str] = None,
        source_id: Optional[str] = None,
        extra_data: Optional[Dict[str, Any]] = None
    ) -> Any:
        """Add a new entity to the knowledge graph."""
        entity_data = dict(extra_data or {})
        if type is not None:
            entity_data["entity_type"] = type
        if description is not None:
            entity_data["description"] = description
        if source_id is not None:
            entity_data["source_id"] = source_id

        data = EntityCreateRequestEntityData.from_dict(entity_data)
        body = EntityCreateRequest(entity_name=name, entity_data=data)
        return await self._execute_op(async_create_entity, f"create_entity_{name}", body=body)

    async def delete_entity(self, name: str) -> Any:
        """Remove an entity from the knowledge graph."""
        body = DeleteEntityRequest(entity_name=name)
        return await self._execute_op(async_delete_entity, f"delete_entity_{name}", body=body)

    async def delete_by_doc(self, doc_id: str) -> Any:
        """Remove all graph elements associated with a document ID."""
        body = DeleteDocRequest(doc_ids=[doc_id])
        return await self._execute_op(async_delete_by_doc_id, f"delete_doc_{doc_id}", body=body)

    async def edit_entity(
        self,
        name: str,
        type: Optional[str] = None,
        description: Optional[str] = None,
        source_id: Optional[str] = None,
        extra_data: Optional[Dict[str, Any]] = None,
        allow_rename: bool = False,
        allow_merge: bool = False
    ) -> Any:
        """Update an existing entity."""
        entity_data = dict(extra_data or {})
        if type is not None:
            entity_data["entity_type"] = type
        if description is not None:
            entity_data["description"] = description
        if source_id is not None:
            entity_data["source_id"] = source_id

        data = EntityUpdateRequestUpdatedData.from_dict(entity_data)
        body = EntityUpdateRequest(
            entity_name=name,
            updated_data=data,
            allow_rename=allow_rename,
            allow_merge=allow_merge
        )
        return await self._execute_op(async_edit_entity, f"edit_entity_{name}", body=body)

    async def merge_entities(self, sources: List[str], target: str, strategy: Dict[str, str]) -> Any:
        """Merge multiple entities into a single target entity."""
        # Strategy argument is kept for API compatibility but ignored as the server API handles it differently now
        body = MergeEntitiesRequest(
            entities_to_change=sources,
            entity_to_change_into=target
        )
        return await self._execute_op(async_merge_entities, f"merge_to_{target}", body=body)

    async def manage_relation(self, source: str, target: str, description: str, keywords: str, 
                               relation_type: Optional[str] = None, source_id: Optional[str] = None, 
                               weight: Optional[float] = None, is_edit: bool = False) -> Any:
        """Create or update a relationship between entities."""
        
        if is_edit:
            relation_data = {
                "description": description,
                "keywords": keywords,
                "weight": weight
            }
            if relation_type is not None:
                relation_data["relation_type"] = relation_type
            if source_id is not None:
                relation_data["source_id"] = source_id

            data = RelationUpdateRequestUpdatedData.from_dict(
                {k: v for k, v in relation_data.items() if v is not None}
            )
            body = RelationUpdateRequest(
                source_id=source,
                target_id=target,
                updated_data=data
            )
            return await self._execute_op(
                async_edit_relation, 
                f"edit_rel_{source}_{target}", 
                body=body
            )
        else:
            relation_data = {
                "description": description,
                "keywords": keywords,
                "weight": weight,
                "source_id": source_id
            }
            if relation_type is not None:
                relation_data["relation_type"] = relation_type

            data = RelationCreateRequestRelationData.from_dict(
                {k: v for k, v in relation_data.items() if v is not None}
            )
            body = RelationCreateRequest(
                source_entity=source,
                target_entity=target,
                relation_data=data
            )
            return await self._execute_op(
                async_create_relation, 
                f"create_rel_{source}_{target}", 
                body=body
            )

    async def check_health(self) -> Any:
        """Check if the LightRAG service is healthy."""
        return await self._execute_op(async_get_health, "health_check")

    def _auth_headers(self) -> Dict[str, str]:
        """Build auth headers without exposing the secret in diagnostics."""
        if not self._runtime_token:
            return {}
        value = (
            f"{self._runtime_auth_prefix} {self._runtime_token}"
            if self._runtime_auth_prefix
            else self._runtime_token
        )
        return {self._runtime_auth_header: value}

    async def _ensure_login_token(self) -> None:
        """Use username/password to obtain a Bearer token when no API key is configured."""
        if self._runtime_token or not (self.settings.username and self.settings.password):
            return
        if self._login_attempted:
            return

        self._login_attempted = True
        async with httpx.AsyncClient(
            base_url=self.settings.base_url,
            verify=False,
            timeout=10.0
        ) as client:
            response = await client.post(
                "/login",
                data={
                    "username": self.settings.username,
                    "password": self.settings.password,
                    "grant_type": "password",
                },
            )

        if response.status_code != 200:
            raise APIResponseError(
                "LightRAG login failed",
                status_code=response.status_code,
                details=response.text
            )

        payload = response.json()
        token = payload.get("access_token") or payload.get("token")
        if not token:
            raise APIResponseError(
                "LightRAG login response did not include an access token",
                status_code=response.status_code,
                details=str(payload)
            )

        self._runtime_token = token
        self._runtime_auth_header = "Authorization"
        self._runtime_auth_prefix = (payload.get("token_type") or "Bearer").capitalize()
        self.client = self._new_authenticated_client(
            self._runtime_token,
            self._runtime_auth_header,
            self._runtime_auth_prefix
        )

    async def diagnose_connection(self) -> Dict[str, Any]:
        """Probe public and protected endpoints using the current MCP configuration."""
        login_error = None
        try:
            await self._ensure_login_token()
        except APIResponseError as e:
            login_error = str(e)
        headers = self._auth_headers()

        async def probe(method: str, path: str, **kwargs) -> Dict[str, Any]:
            try:
                async with httpx.AsyncClient(
                    base_url=self.settings.base_url,
                    verify=False,
                    timeout=10.0,
                    headers=headers
                ) as client:
                    response = await client.request(method, path, **kwargs)
                body = response.text
                return {
                    "status_code": response.status_code,
                    "reason": response.reason_phrase,
                    "content_type": response.headers.get("content-type"),
                    "body": body[:1000],
                }
            except httpx.RequestError as e:
                return {
                    "status_code": None,
                    "reason": "request_error",
                    "error": str(e),
                }

        health = await probe("GET", "/health")
        pipeline = await probe("GET", "/documents/pipeline_status")
        paginated = await probe(
            "POST",
            "/documents/paginated",
            json={"page": 1, "page_size": 10, "sort_field": "updated_at", "sort_direction": "desc"}
        )
        graph_labels = await probe("GET", "/graph/label/list")
        graph_data = await probe("GET", "/graphs", params={"label": "*", "max_depth": 3, "max_nodes": 1000})

        diagnosis = "unknown"
        protected_status = pipeline.get("status_code")
        if health.get("status_code") != 200:
            diagnosis = "base_url_or_service_unreachable"
        elif login_error:
            diagnosis = "login_failed"
        elif protected_status in [401, 403]:
            diagnosis = "authentication_failed_or_missing"
        elif protected_status == 503 or paginated.get("status_code") == 503:
            diagnosis = "protected_api_service_unavailable"
        elif graph_labels.get("status_code") == 503 and graph_data.get("status_code") == 200:
            diagnosis = "graph_label_endpoint_unavailable_use_graphs_fallback"
        elif protected_status and 200 <= protected_status < 300:
            diagnosis = "connection_and_auth_ok"

        return {
            "base_url": self.settings.base_url,
            "auth": {
                "configured": bool(self._runtime_token or (self.settings.username and self.settings.password)),
                "method": "api_key" if self.settings.api_key else ("login" if self.settings.username else None),
                "header": self._runtime_auth_header if self._runtime_token else None,
                "prefix": self._runtime_auth_prefix if self._runtime_token else None,
                "login_error": login_error,
            },
            "diagnosis": diagnosis,
            "probes": {
                "health": health,
                "pipeline_status": pipeline,
                "documents_paginated": paginated,
                "graph_label_list": graph_labels,
                "graphs": graph_data,
            },
        }

    async def upsert_document(self, file_path: Union[str, Path]) -> Dict[str, Any]:
        """
        Intelligently upload a document:
        - If document doesn't exist: upload it
        - If document exists and is identical: skip upload
        - If document exists but was modified: delete and re-upload
        
        Returns a dict with 'action' (created/skipped/updated), 'doc_id', and optional 'reason'.
        """
        path = Path(file_path)
        if not path.exists():
            raise ResourceNotFoundError(f"File not found: {file_path}")
        
        # Get local file info
        local_content = path.read_bytes()
        local_size = len(local_content)
        # Also compute stripped size to handle trailing whitespace differences
        local_size_stripped = len(local_content.rstrip())
        file_name = path.name
        
        # Check if document already exists
        existing_doc = await self.find_document_by_file_name(file_name)
        
        if existing_doc is None:
            # Document doesn't exist, upload it
            result = await self.upload_file(file_path)
            logger.info(f"Created new document: {file_name}")
            return {
                "action": "created",
                "file_name": file_name,
                "result": result
            }
        
        # Document exists, check if it's identical
        existing_size = getattr(existing_doc, "content_length", None)
        doc_id = getattr(existing_doc, "id", None)
        
        # Compare by content length - handle trailing whitespace differences
        # Server may store content with/without trailing newline
        if existing_size is not None:
            # Check exact match OR match after stripping trailing whitespace
            sizes_match = (
                existing_size == local_size or 
                existing_size == local_size_stripped or
                abs(existing_size - local_size) <= 2  # Allow 2-byte tolerance for newline variations (\n vs \r\n)
            )
            if sizes_match:
                logger.info(f"Document {file_name} already exists with same content (skipped)")
                return {
                    "action": "skipped",
                    "reason": "document already exists with identical content",
                    "doc_id": doc_id,
                    "file_name": file_name
                }
        
        # Document exists but was modified, delete and re-upload
        if doc_id:
            logger.info(f"Document {file_name} was modified, deleting old version...")
            await self.delete_by_doc(doc_id)
        
        result = await self.upload_file(file_path)
        logger.info(f"Updated document: {file_name}")
        return {
            "action": "updated",
            "old_doc_id": doc_id,
            "file_name": file_name,
            "result": result
        }
