"""向 Codex 暴露受限 Elasticsearch 接口日志查询与分析工具。"""

from pathlib import Path
from typing import Any, Callable

import yaml
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from es_mcp.client import (
    ElasticsearchQueryError,
    analyze_api_trend as client_analyze_api_trend,
    analyze_slow_requests as client_analyze_slow_requests,
    create_client,
    get_api_distribution as client_get_api_distribution,
    get_api_statistics as client_get_api_statistics,
    get_error_apis as client_get_error_apis,
    get_index_stats as client_get_index_stats,
    get_log_detail,
    get_shard_capacity as client_get_shard_capacity,
    get_slow_apis as client_get_slow_apis,
    search_error_logs as client_search_error_logs,
    search_logs,
)
from es_mcp.config import ElasticsearchConfigurationError, load_environment
from es_mcp.diagnostics import (
    cluster_health, node_health, shard_allocation, index_templates,
    index_mapping, index_settings, index_aliases, index_retention,
)
from es_mcp.analysis import compare_periods, latency_percentiles, error_samples, trace_request, topology


def _with_client(env_file: Path, environment: str, operation: Callable[[Any, Any], dict]) -> dict:
    """统一的客户端操作包装器。"""
    client = None
    try:
        config = load_environment(env_file, environment)
        client = create_client(config)
        return operation(client, config)
    except (ElasticsearchConfigurationError, ElasticsearchQueryError) as error:
        raise ValueError(str(error)) from error
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def create_server(
    env_file: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 18005,
    allowed_hosts: list[str] | None = None,
) -> FastMCP:
    """创建 MCP Server 实例。"""
    server = FastMCP(
        "elasticsearch",
        instructions="按环境进行受限、只读 Elasticsearch 接口日志查询与分析。",
        host=host,
        port=port,
        transport_security=(
            TransportSecuritySettings(allowed_hosts=allowed_hosts)
            if allowed_hosts
            else None
        ),
    )

    @server.tool(
        name="list_es_environments",
        description="列出已配置 Elasticsearch 的环境和索引模式。",
    )
    def list_es_environments() -> dict:
        try:
            data = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
            values = [
                {
                    "environment": item["env_name"],
                    "hosts": item["es"].get("hosts", []),
                    "index_allowlist": item["es"].get("index_allowlist", []),
                }
                for item in data.get("env", [])
                if isinstance(item, dict)
                and isinstance(item.get("env_name"), str)
                and isinstance(item.get("es"), dict)
            ]
            return {"environments": values}
        except OSError as error:
            raise ValueError("无法读取 env.yaml") from error

    @server.tool(
        name="search_api_logs",
        description="按条件搜索接口日志。index_type: openapi 或 thirdapi",
    )
    def search_api_logs(
        environment: str,
        index_type: str,
        start_time: str | None = None,
        end_time: str | None = None,
        route: str | None = None,
        status_code: str | None = None,
        invoke_user: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> dict:
        def operation(client: Any, config: Any) -> dict:
            return search_logs(
                client,
                config,
                index_type,
                start_time=start_time,
                end_time=end_time,
                route=route,
                status_code=status_code,
                invoke_user=invoke_user,
                page=page,
                size=size,
            )

        return _with_client(env_file, environment, operation)

    @server.tool(
        name="get_api_log_detail",
        description="获取单条接口日志详情。",
    )
    def get_api_log_detail(environment: str, index_type: str, log_id: str) -> dict:
        def operation(client: Any, config: Any) -> dict:
            return get_log_detail(client, config, index_type, log_id)

        return _with_client(env_file, environment, operation)

    @server.tool(
        name="get_index_stats",
        description="获取 Elasticsearch 索引统计信息。",
    )
    def get_index_stats(environment: str, index_type: str) -> dict:
        def operation(client: Any, config: Any) -> dict:
            return client_get_index_stats(client, config, index_type)

        return _with_client(env_file, environment, operation)

    @server.tool(
        name="get_api_statistics",
        description="获取接口调用统计（次数、平均耗时、错误率）。",
    )
    def get_api_statistics(
        environment: str,
        index_type: str,
        start_time: str | None = None,
        end_time: str | None = None,
        route: str | None = None,
    ) -> dict:
        def operation(client: Any, config: Any) -> dict:
            return client_get_api_statistics(
                client,
                config,
                index_type,
                start_time=start_time,
                end_time=end_time,
                route=route,
            )

        return _with_client(env_file, environment, operation)

    @server.tool(
        name="get_slow_apis",
        description="获取慢查询接口排行（Top N）。",
    )
    def get_slow_apis(
        environment: str,
        index_type: str,
        start_time: str | None = None,
        end_time: str | None = None,
        top_n: int = 10,
    ) -> dict:
        def operation(client: Any, config: Any) -> dict:
            return client_get_slow_apis(
                client,
                config,
                index_type,
                start_time=start_time,
                end_time=end_time,
                top_n=top_n,
            )

        return _with_client(env_file, environment, operation)

    @server.tool(
        name="get_error_apis",
        description="获取错误接口排行（Top N）。",
    )
    def get_error_apis(
        environment: str,
        index_type: str,
        start_time: str | None = None,
        end_time: str | None = None,
        top_n: int = 10,
    ) -> dict:
        def operation(client: Any, config: Any) -> dict:
            return client_get_error_apis(
                client,
                config,
                index_type,
                start_time=start_time,
                end_time=end_time,
                top_n=top_n,
            )

        return _with_client(env_file, environment, operation)

    @server.tool(
        name="analyze_api_trend",
        description="分析接口调用趋势（按小时或天）。",
    )
    def analyze_api_trend(
        environment: str,
        index_type: str,
        start_time: str | None = None,
        end_time: str | None = None,
        route: str | None = None,
        interval: str = "hour",
    ) -> dict:
        def operation(client: Any, config: Any) -> dict:
            return client_analyze_api_trend(
                client,
                config,
                index_type,
                start_time=start_time,
                end_time=end_time,
                route=route,
                interval=interval,
            )

        return _with_client(env_file, environment, operation)

    @server.tool(
        name="get_api_distribution",
        description="获取接口调用分布统计（按状态码、应用、平台）。",
    )
    def get_api_distribution(
        environment: str,
        index_type: str,
        start_time: str | None = None,
        end_time: str | None = None,
        group_by: str = "statusCode",
    ) -> dict:
        def operation(client: Any, config: Any) -> dict:
            return client_get_api_distribution(
                client,
                config,
                index_type,
                start_time=start_time,
                end_time=end_time,
                group_by=group_by,
            )

        return _with_client(env_file, environment, operation)

    @server.tool(
        name="search_error_logs",
        description="搜索错误日志（HTTP错误或业务错误）。",
    )
    def search_error_logs(
        environment: str,
        index_type: str,
        start_time: str | None = None,
        end_time: str | None = None,
        error_type: str = "all",
        keyword: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> dict:
        def operation(client: Any, config: Any) -> dict:
            return client_search_error_logs(
                client,
                config,
                index_type,
                start_time=start_time,
                end_time=end_time,
                error_type=error_type,
                keyword=keyword,
                page=page,
                size=size,
            )

        return _with_client(env_file, environment, operation)

    @server.tool(
        name="analyze_slow_requests",
        description="分析慢请求（耗时分布、请求详情）。",
    )
    def analyze_slow_requests(
        environment: str,
        index_type: str,
        start_time: str | None = None,
        end_time: str | None = None,
        min_duration: int = 1000,
        route: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> dict:
        def operation(client: Any, config: Any) -> dict:
            return client_analyze_slow_requests(
                client,
                config,
                index_type,
                start_time=start_time,
                end_time=end_time,
                min_duration=min_duration,
                route=route,
                page=page,
                size=size,
            )

        return _with_client(env_file, environment, operation)

    @server.tool(
        name="get_shard_capacity",
        description="获取白名单日志索引的分片状态及 ES 节点磁盘使用情况。",
    )
    def get_shard_capacity(
        environment: str,
        index_type: str,
        shard_limit: int = 100,
    ) -> dict:
        def operation(client: Any, config: Any) -> dict:
            return client_get_shard_capacity(
                client, config, index_type, shard_limit=shard_limit
            )

        return _with_client(env_file, environment, operation)

    @server.tool(name="get_cluster_health", description="查询 ES 集群健康状态和分片摘要。")
    def get_cluster_health(environment: str) -> dict:
        return _with_client(env_file, environment, cluster_health)

    @server.tool(name="get_node_health", description="查询 ES 节点 CPU、JVM、磁盘和线程池健康摘要。")
    def get_node_health(environment: str) -> dict:
        return _with_client(env_file, environment, node_health)

    @server.tool(name="get_shard_allocation", description="查询白名单索引的分片分配和未分配原因。")
    def get_shard_allocation(environment: str, index_type: str, unassigned_only: bool = False, limit: int = 200) -> dict:
        return _with_client(env_file, environment, lambda c, cfg: shard_allocation(c, cfg, index_type, unassigned_only=unassigned_only, limit=limit))

    @server.tool(name="get_index_mapping", description="查询白名单日志索引字段映射。")
    def get_index_mapping(environment: str, index_type: str) -> dict:
        return _with_client(env_file, environment, lambda c, cfg: index_mapping(c, cfg, index_type))

    @server.tool(name="get_index_settings", description="查询白名单日志索引关键设置。")
    def get_index_settings(environment: str, index_type: str) -> dict:
        return _with_client(env_file, environment, lambda c, cfg: index_settings(c, cfg, index_type))

    @server.tool(name="get_index_aliases", description="查询白名单日志索引别名。")
    def get_index_aliases(environment: str, index_type: str) -> dict:
        return _with_client(env_file, environment, lambda c, cfg: index_aliases(c, cfg, index_type))

    @server.tool(name="get_index_retention", description="查询白名单日志索引数量、文档量和存储量。")
    def get_index_retention(environment: str, index_type: str, limit: int = 200) -> dict:
        return _with_client(env_file, environment, lambda c, cfg: index_retention(c, cfg, index_type, limit=limit))

    @server.tool(name="get_index_templates", description="查询 Elasticsearch 索引模板，兼容 7.10 legacy template。")
    def get_index_templates(environment: str, name: str | None = None) -> dict:
        return _with_client(env_file, environment, lambda c, cfg: index_templates(c, cfg, name))

    @server.tool(name="compare_api_periods", description="对比两个时间段的接口请求量和延迟分位数。")
    def compare_api_periods(environment: str, index_type: str, first_start: str, first_end: str, second_start: str, second_end: str) -> dict:
        return _with_client(env_file, environment, lambda c, cfg: compare_periods(c, cfg, index_type, first_start=first_start, first_end=first_end, second_start=second_start, second_end=second_end))

    @server.tool(name="get_api_latency_percentiles", description="按接口查询 P50/P90/P95/P99 延迟。")
    def get_api_latency_percentiles(environment: str, index_type: str, start_time: str | None = None, end_time: str | None = None, route: str | None = None, top_n: int = 50) -> dict:
        return _with_client(env_file, environment, lambda c, cfg: latency_percentiles(c, cfg, index_type, start_time=start_time, end_time=end_time, route=route, top_n=top_n))

    @server.tool(name="get_api_error_samples", description="查询错误接口的少量脱敏样本。")
    def get_api_error_samples(environment: str, index_type: str, start_time: str | None = None, end_time: str | None = None, top_n: int = 20) -> dict:
        return _with_client(env_file, environment, lambda c, cfg: error_samples(c, cfg, index_type, start_time=start_time, end_time=end_time, top_n=top_n))

    @server.tool(name="trace_api_request", description="按请求 ID 查询接口调用链日志。")
    def trace_api_request(environment: str, index_type: str, request_id: str, start_time: str | None = None, end_time: str | None = None) -> dict:
        return _with_client(env_file, environment, lambda c, cfg: trace_request(c, cfg, index_type, request_id=request_id, start_time=start_time, end_time=end_time))

    @server.tool(name="get_api_topology", description="按接口和调用方聚合接口拓扑。")
    def get_api_topology(environment: str, index_type: str, start_time: str | None = None, end_time: str | None = None, top_n: int = 50) -> dict:
        return _with_client(env_file, environment, lambda c, cfg: topology(c, cfg, index_type, start_time=start_time, end_time=end_time, top_n=top_n))

    return server
