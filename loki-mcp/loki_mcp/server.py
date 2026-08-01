"""向 Codex 暴露只读 Loki 查询工具。"""

from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from loki_mcp.client import LokiQueryError, query_logs
from loki_mcp.config import LokiConfigurationError, load_environment


def parse_timestamp(value: str) -> datetime:
    """解析包含时区偏移的 ISO 8601 时间。"""

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("时间必须是 ISO 8601 格式") from error
    if parsed.tzinfo is None:
        raise ValueError("时间必须包含时区，例如 +08:00")
    return parsed


def create_server(
    env_file: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    allowed_hosts: list[str] | None = None,
) -> FastMCP:
    """创建使用指定环境配置文件的 MCP 服务。"""

    server = FastMCP(
        "loki",
        instructions="按环境读取 env.yaml，仅执行受限的只读 Loki 日志范围查询。",
        host=host,
        port=port,
        transport_security=(TransportSecuritySettings(allowed_hosts=allowed_hosts) if allowed_hosts else None),
    )

    @server.tool(
        name="query_loki_logs",
        description="按环境读取默认 LogQL，并在指定时间范围查询 Loki 日志。",
    )
    def query_loki_logs(
        environment: str,
        start: str,
        end: str,
        limit: int = 200,
        contains: str | None = None,
    ) -> dict:
        """查询单个环境的 Loki 日志；start 与 end 必须包含时区。"""

        try:
            config = load_environment(env_file, environment)
            return query_logs(
                config,
                parse_timestamp(start),
                parse_timestamp(end),
                limit=limit,
                contains=contains,
            )
        except (LokiConfigurationError, LokiQueryError, ValueError) as error:
            raise ValueError(str(error)) from error

    return server
