# LightRAG MCP Server

这是一个面向 [LightRAG](https://github.com/HKUDS/LightRAG) 的 Model Context Protocol (MCP) 服务端。它让支持 MCP 的 AI 助手可以通过标准工具接口查询 LightRAG 知识图谱、管理文档、维护实体和关系。

该项目特别适合 Obsidian 知识库同步场景：内置的智能 `upsert` 机制可以判断文档是否新增、未变化或已修改，避免重复上传和无意义的重新索引。

## 功能特性

- **智能更新**：通过 `upsert` 检测文档变化，未变化时跳过，变化后删除旧版本并重新上传。
- **知识图谱查询**：支持当前 LightRAG 查询模式：`mix`、`local`、`global`、`hybrid`、`naive`、`bypass`。
- **文档导入**：支持导入文本、文件和目录批量扫描。
- **实体管理**：支持创建、修改、合并和删除知识图谱实体。
- **关系管理**：支持创建和更新实体之间的关系。
- **连接可靠性**：API 调用带重试和错误处理。
- **灵活配置**：支持通过命令行参数或环境变量配置 LightRAG 服务地址和认证信息。

## 安装

```bash
# 克隆仓库
git clone https://github.com/enriquecatala/mcp-lightrag.git
cd mcp-lightrag

# 安装依赖
uv sync
```

## 快速开始

1. **先启动 LightRAG 服务**

   MCP 服务启动前，LightRAG API 必须已经运行。

2. **启动 MCP 服务**

   ```bash
   uv run mcp-lightrag --host localhost --port 9621
   ```

3. **在 MCP 客户端中连接**

   本服务默认使用 stdio transport，适合接入 Claude Desktop、Cursor、Codex 等支持 MCP 的客户端。

## 配置

| 选项 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `--host` | `LIGHTRAG_HOST` | `localhost` | LightRAG API 主机 |
| `--port` | `LIGHTRAG_PORT` | `9621` | LightRAG API 端口 |
| `--api-key` | `LIGHTRAG_API_KEY` | 无 | 可选 API key 或 token |
| 仅环境变量 | `LIGHTRAG_API_KEY_HEADER` | `Authorization` | 认证 header 名称，例如 `X-API-Key` |
| 仅环境变量 | `LIGHTRAG_API_KEY_PREFIX` | `Bearer` | 认证前缀；若使用裸 API key header，可设置为空字符串 |
| `--log-level` | 无 | `INFO` | 日志级别 |

默认认证方式为：

```http
Authorization: Bearer <LIGHTRAG_API_KEY>
```

如果你的 LightRAG 部署使用 `X-API-Key`，可以这样配置：

```bash
LIGHTRAG_API_KEY=your_api_key
LIGHTRAG_API_KEY_HEADER=X-API-Key
LIGHTRAG_API_KEY_PREFIX=
```

## 配置为 MCP Server

在 MCP 客户端配置中加入如下内容。该示例使用 `uv` 从源码目录启动服务。

```json
{
  "mcpServers": {
    "mcp-lightrag": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/mcp-lightrag",
        "run",
        "mcp-lightrag",
        "--host",
        "localhost",
        "--port",
        "9621"
      ],
      "env": {
        "LIGHTRAG_API_KEY": "optional_api_key"
      }
    }
  }
}
```

将 `/absolute/path/to/mcp-lightrag` 替换为本仓库在你机器上的绝对路径。

## 智能文档处理

`upsert_document` 适合用于持续同步 Obsidian Vault 或其他本地知识库目录：

- **新文件**：上传并交给 LightRAG 索引。
- **未变化文件**：检测到内容长度一致后跳过，节省时间和资源。
- **已修改文件**：删除旧文档记录，再上传新版本。

这种机制让 AI 助手可以高效维护一批持续变化的知识文档。

## 可用工具

### 搜索与查询

- `query_knowledge_graph`：执行 RAG 查询。支持 `mix`、`local`、`global`、`hybrid`、`naive`、`bypass`。兼容旧别名：`semantic` 会映射到 `naive`，`keyword` 会映射到 `hybrid`。

### 文档管理

- `ingest_text`：直接将文本内容写入知识图谱。
- `ingest_file`：索引一个本地文件，文件路径必须对 MCP 服务进程可见。
- `upload_and_index`：上传本地文件到 LightRAG 服务端输入目录并触发索引。
- `ingest_batch`：按目录批量导入文件，支持递归和过滤规则。
- `upsert_document`：智能文档上传，新建、跳过或更新文档。
- `find_document`：按文件名或路径查找文档状态和详情。
- `get_latest_documents`：分页获取最近更新的文档。
- `list_all_docs`：列出系统中的所有文档；文档量大时可能较慢。
- `check_indexing_status`：检查后台索引管线是否空闲或忙碌。

### 图谱操作

- `create_entities`：手动创建实体。
- `modify_entities`：更新实体属性。
- `remove_entities`：删除指定实体。
- `unify_entities`：将多个实体合并为一个规范实体。
- `connect_entities`：创建或更新实体之间的关系。
- `purge_by_document`：按文档 ID 删除文档及其关联的图谱数据。
- `get_graph_metadata`：查看图谱元数据，例如可用标签和关系类型。

### 系统工具

- `verify_server_health`：检查 LightRAG API 是否可访问且健康。

## 开发

```bash
# 安装开发依赖
uv sync --all-extras

# 运行测试
uv run python -m pytest

# 运行 lint
uv run ruff check src/
```

## 发布

发布新版本到 PyPI 的基本流程：

1. 更新 `pyproject.toml` 中的版本号。
2. 构建包：

   ```bash
   uv run python -m build
   ```

3. 上传到 PyPI：

   ```bash
   uv run twine upload dist/*
   ```

## 更新生成客户端

当 LightRAG API 发生变化时，可以使用 `openapi-python-client` 重新生成客户端代码。确保 LightRAG 服务正在运行，例如 `http://localhost:9621`，然后执行：

```bash
uv tool run openapi-python-client generate \
  --url http://localhost:9621/openapi.json \
  --output-path src/mcp_lightrag/client/light_rag_server_api_client \
  --meta none \
  --overwrite
```

该命令会根据最新 OpenAPI 规范更新 `src/mcp_lightrag/client/light_rag_server_api_client` 下的生成代码。

## 许可证

MIT
