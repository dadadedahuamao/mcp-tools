# mcp-tools

`mcp-tools` 是一组面向生产排障的只读 MCP（Model Context Protocol）服务。各服务可在本地通过 `stdio` 方式供 MCP 客户端调用，也可通过 Docker 以 Streamable HTTP 方式部署到服务器，供 Codex 等客户端远程连接。

| 服务目录 | 服务名称 | 用途 | HTTP 端口 |
| --- | --- | --- | --- |
| `db-mcp` | 数据库 MCP | 受限只读的数据库查询 | `18000` |
| `loki-mcp` | Loki MCP | Grafana Loki 日志查询 | `18001` |
| `k8s-mcp` | Kubernetes MCP | Kubernetes 集群信息查询 | `18002` |
| `redis-mcp` | Redis MCP | 受限只读的 Redis 排障 | `18003` |
| `obs-mcp` | OBS MCP | S3 兼容的华为 OBS 查询 | `18004` |
| `es-mcp` | Elasticsearch MCP | 接口日志查询与分析 | `18005` |

> 配置文件位于各服务的 `config/env.yaml`。其中可能包含连接凭据，不要将生产密钥提交到版本库。

## 本地开发

### Python 版本

所有服务均要求 **Python 3.12 或更高版本**。请先确认版本：

```powershell
python --version
```

### 创建虚拟环境并安装依赖

每个服务都是独立 Python 项目，应在对应服务目录中创建虚拟环境。以下以 `db-mcp` 为例，其他服务只需替换目录名：

```powershell
cd db-mcp
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install .
```

`python -m pip install .` 会一次性安装该服务 `pyproject.toml` 中声明的运行依赖。需要测试依赖时，执行：

```powershell
python -m pip install ".[test]"
```

退出虚拟环境：

```powershell
deactivate
```

## Docker 部署

每个服务目录均提供 `deploy/Dockerfile`、`deploy/docker-compose.yaml` 和 `.env.example`。部署前需要服务器已安装 Docker Engine 和可用的 `docker-compose` 命令。

以下以 `db-mcp` 为例：

1. 进入服务根目录。`.env` 必须放在服务根目录，不能只放在 `deploy` 目录以外的任意位置。

   ```powershell
   cd db-mcp
   ```

2. 创建并编辑服务根目录中的 `.env`。

   ```bash
   cp .env.example .env
   ```

3. 编辑 `.env`：将 `DB_MCP_ALLOWED_HOST` 改为客户端实际访问的 `服务器地址:18000`；按需将 `DB_MCP_BIND_ADDRESS` 限制为内网监听地址。`*_MCP_ALLOWED_HOST` 是必填项，必须与客户端请求中的 `Host` 一致；<span style="color: red;"><strong>不要填写 `127.0.0.1`</strong></span>，除非客户端仅在部署服务器本机访问。例如服务器 IP 为 `10.189.91.153` 时，DB MCP 应填写 `DB_MCP_ALLOWED_HOST=10.189.91.153:18000`。

4. 检查并填写 `config/env.yaml` 中的数据库数据源配置。配置文件会以只读方式挂载到容器。

5. 从服务根目录显式指定 `.env` 与 Compose 文件，构建并启动服务：

   ```powershell
   docker-compose --env-file .env -f deploy/docker-compose.yaml up -d --build --force-recreate
   docker-compose --env-file .env -f deploy/docker-compose.yaml ps
   docker-compose --env-file .env -f deploy/docker-compose.yaml logs -f
   ```

   `--env-file .env` 必不可省略：Compose 会在创建容器前解析 `${...}` 变量，容器内挂载 `.env` 无法为该阶段提供变量。修改 `.env` 后也应保留 `--force-recreate`，以便将新的 `--allowed-host` 参数传入新容器。

停止服务：

```powershell
docker-compose --env-file .env -f deploy/docker-compose.yaml down
```

其他服务使用相同流程；对应的端口和 `.env` 变量如下：

| 服务 | 端口 | `.env` 中必须设置的 Host 变量 |
| --- | --- | --- |
| `db-mcp` | `18000` | `DB_MCP_ALLOWED_HOST` |
| `loki-mcp` | `18001` | `LOKI_MCP_ALLOWED_HOST` |
| `k8s-mcp` | `18002` | `K8S_MCP_ALLOWED_HOST` |
| `redis-mcp` | `18003` | `REDIS_MCP_ALLOWED_HOST` |
| `obs-mcp` | `18004` | `OBS_MCP_ALLOWED_HOST` |
| `es-mcp` | `18005` | `ES_MCP_ALLOWED_HOST` |

例如，ES MCP 在服务器上的部署命令为：

```bash
cd /soft/mcp-tools/es-mcp
docker-compose --env-file .env -f deploy/docker-compose.yaml up -d --force-recreate
```

若 Codex 通过 `http://10.189.91.153:18005/mcp` 访问 ES MCP，则 `.env` 中应设置：

```dotenv
ES_MCP_ALLOWED_HOST=10.189.91.153:18005
```

`k8s-mcp` 会将整个 `config` 目录挂载到容器；除 `env.yaml` 外，还需将其引用的 kubeconfig 文件一并放在该目录中。建议通过防火墙、安全组或反向代理限制端口仅对需要访问的网络开放。

## 在 Codex 中配置远程 MCP

先按上述步骤部署所需服务，确认客户端可访问对应端口。然后打开 Codex 用户配置文件（通常为 `%USERPROFILE%\.codex\config.toml`），在文件中追加所需服务配置。将示例中的 `SERVER_IP_OR_DOMAIN` 替换为服务器的 IP 或域名。

```toml
[mcp_servers.db]
url = "http://SERVER_IP_OR_DOMAIN:18000/mcp"

[mcp_servers.loki]
url = "http://SERVER_IP_OR_DOMAIN:18001/mcp"

[mcp_servers.k8s]
url = "http://SERVER_IP_OR_DOMAIN:18002/mcp"

[mcp_servers.redis]
url = "http://SERVER_IP_OR_DOMAIN:18003/mcp"

[mcp_servers.obs]
url = "http://SERVER_IP_OR_DOMAIN:18004/mcp"

[mcp_servers.es]
url = "http://SERVER_IP_OR_DOMAIN:18005/mcp"
```

保存配置后重启 Codex，或新建一个任务使配置重新加载。只配置实际需要的服务；生产环境建议使用 HTTPS 反向代理，并为入口增加访问控制。

Codex 的 MCP 配置格式请以 [官方 OpenAI 文档](https://developers.openai.com/) 为准。

## Oracle 数据库诊断

`db-mcp` 为 Oracle 数据源提供以下受限诊断工具：

- `list_indexes`：查看白名单 schema 中表的索引及索引列；可按表名过滤。
- `search_sql`：查看近一天或指定 `sql_id` 的 SQL 游标性能摘要；不返回 SQL 文本。
- `get_sql_detail`：按 `sql_id` 返回 SQL 文本和 child cursor 统计，可继续用 `explain_query` 获取执行计划。
- `get_object_health`：查看无效对象、表统计和索引统计。

普通 `query` 仍不允许访问 `SYS` 或动态性能视图。请让 DBA 按最小权限原则为 db-mcp 账号授予：`V_$SQL`、`V_$SESSION`、`V_$LOCK` 的 `SELECT`，以及 `DBMS_XPLAN` 的 `EXECUTE`。不要仅为 db-mcp 授予 `SELECT ANY DICTIONARY` 或 `SELECT_CATALOG_ROLE` 等广泛目录权限。若调用返回 `ORA-01031`，应由 DBA 补充对应视图的精确授权。

## 技术文档

详细的架构分析、技术栈解析和实现细节请参阅：[TECHNICAL_ANALYSIS.md](TECHNICAL_ANALYSIS.md)

## Elasticsearch 接口日志分析

`es-mcp` 提供受限只读的 Elasticsearch 接口日志查询与分析功能，支持 OpenApi 接口日志和第三方接口日志。

### 工具列表

**基础查询工具**：
- `list_es_environments`：列出已配置的 ES 环境
- `search_api_logs`：按条件搜索接口日志
- `get_api_log_detail`：获取单条日志详情
- `get_index_stats`：获取索引统计信息
- `get_shard_capacity`：获取白名单日志索引的主/副分片状态、单分片大小及节点磁盘使用情况
- `get_cluster_health`：查询集群健康和未分配分片摘要
- `get_node_health`：查询节点 CPU、JVM、磁盘和线程池摘要
- `get_shard_allocation`：查询分片分配及未分配原因
- `get_index_mapping`：查询索引字段映射
- `get_index_settings`：查询索引关键设置
- `get_index_aliases`：查询索引别名
- `get_index_retention`：查询索引数量、文档量和存储量
- `get_index_templates`：查询索引模板（兼容 ES 7.10 legacy template）

**统计分析工具**：
- `get_api_statistics`：接口调用统计（次数、平均耗时、错误率）
- `get_slow_apis`：慢查询接口排行
- `get_error_apis`：错误接口排行

**问题定位工具**：
- `analyze_api_trend`：接口调用趋势分析（按小时/天）
- `get_api_distribution`：接口调用分布统计
- `search_error_logs`：搜索错误日志
- `analyze_slow_requests`：慢请求分析
- `compare_api_periods`：对比两个时间段的调用量和延迟
- `get_api_latency_percentiles`：按接口查询延迟分位数
- `get_api_error_samples`：查询脱敏错误样本
- `trace_api_request`：按请求 ID 查询调用链日志
- `get_api_topology`：聚合接口和调用方拓扑

### 索引类型

- `openapi`：OpenApi 接口日志（索引模式：`*open_api_log*`）
- `thirdapi`：第三方接口日志（索引模式：`*third_api_log*`）

### 配置示例

在 `config/env.yaml` 中添加 `es` 配置块：

```yaml
env:
  - env_name: "UAT环境"
    es:
      hosts:
        - "http://10.180.6.166:9200"
        - "http://10.180.4.209:9200"
        - "http://10.180.5.157:9200"
      username: "admin"
      password: "xcmg#@!MES01"
      index_allowlist:
        - "*open_api_log*"
        - "*third_api_log*"
      timeout_seconds: 30
      max_result_window: 10000
      max_response_bytes: 1048576
```
