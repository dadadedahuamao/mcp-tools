"""向 Codex 暴露受限 Redis 排障工具。"""

from pathlib import Path
from typing import Any, Callable

import yaml
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from redis_mcp.client import (RedisQueryError, create_client, get_clients, get_command_stats,
                              get_overview, get_slowlog, inspect_key, scan_keys)
from redis_mcp.config import RedisConfigurationError, load_environment


def _with_client(env_file: Path, environment: str, operation: Callable[[Any, Any], dict]) -> dict:
    client = None
    try:
        config = load_environment(env_file, environment)
        client = create_client(config)
        return operation(client, config)
    except (RedisConfigurationError, RedisQueryError) as error:
        raise ValueError(str(error)) from error
    finally:
        if client is not None:
            try: client.close()
            except Exception: pass


def create_server(env_file: Path, *, host: str = "127.0.0.1", port: int = 18003,
                  allowed_hosts: list[str] | None = None) -> FastMCP:
    server = FastMCP("redis", instructions="按环境进行受限、只读 Redis 生产排障。", host=host, port=port,
                     transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts) if allowed_hosts else None)

    @server.tool(name="list_redis_environments", description="列出已配置 Redis 的环境和部署模式。")
    def list_redis_environments() -> dict:
        try:
            data = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
            values = [{"environment": item["env_name"], "mode": item["redis"]["mode"]}
                      for item in data.get("env", []) if isinstance(item, dict) and isinstance(item.get("env_name"), str)
                      and isinstance(item.get("redis"), dict) and item["redis"].get("mode") in {"standalone", "sentinel", "cluster"}]
            return {"environments": values}
        except OSError as error: raise ValueError("无法读取 env.yaml") from error

    @server.tool(name="get_redis_overview", description="获取 Redis 服务、内存、复制和键空间概览。")
    def get_redis_overview(environment: str) -> dict:
        return _with_client(env_file, environment, lambda client, config: get_overview(client, config.mode))

    @server.tool(name="scan_redis_keys", description="通过非阻塞 SCAN 分页查找 Redis 键。")
    def scan_redis_keys(environment: str, cursor: int = 0, match: str | None = None, count: int = 100) -> dict:
        return _with_client(env_file, environment, lambda client, _: scan_keys(client, cursor=cursor, match=match, count=count))

    @server.tool(name="inspect_redis_key", description="读取单个 Redis 键的类型、TTL、内存和受限内容摘要。")
    def inspect_redis_key(environment: str, key: str) -> dict:
        return _with_client(env_file, environment, lambda client, _: inspect_key(client, key))

    @server.tool(name="get_redis_slowlog", description="读取最近 Redis 慢日志，不会重置日志。")
    def get_redis_slowlog(environment: str, limit: int = 50) -> dict:
        return _with_client(env_file, environment, lambda client, _: get_slowlog(client, limit))

    @server.tool(name="get_redis_clients", description="读取 Redis 客户端连接摘要。")
    def get_redis_clients(environment: str, limit: int = 50) -> dict:
        return _with_client(env_file, environment, lambda client, _: get_clients(client, limit))

    @server.tool(name="get_redis_command_stats", description="读取 Redis 命令调用和耗时统计。")
    def get_redis_command_stats(environment: str) -> dict:
        return _with_client(env_file, environment, lambda client, _: get_command_stats(client))
    return server
