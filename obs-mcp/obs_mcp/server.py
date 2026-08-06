from pathlib import Path
from typing import Any, Callable

import yaml
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from obs_mcp.client import (ObsQueryError, create_client, get_object_metadata, list_configured_buckets,
                            list_objects, read_text_object)
from obs_mcp.config import ObsConfigurationError, load_environment


def _with_client(env_file: Path, environment: str, operation: Callable[[Any, Any], dict]) -> dict:
    try:
        config = load_environment(env_file, environment)
        return operation(create_client(config), config)
    except (ObsConfigurationError, ObsQueryError) as error:
        raise ValueError(str(error)) from error


def create_server(env_file: Path, *, host: str = "127.0.0.1", port: int = 18004, allowed_hosts: list[str] | None = None) -> FastMCP:
    server = FastMCP("obs", instructions="按环境执行受限、只读的 S3 兼容 OBS 查询。", host=host, port=port, transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts) if allowed_hosts else None)

    @server.tool(name="list_obs_environments", description="列出已配置 OBS 的环境和允许访问的桶。")
    def list_obs_environments() -> dict:
        try:
            data = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
            return {"environments": [{"environment": item["env_name"], "buckets": item["obs"]["buckets"]} for item in data.get("env", []) if isinstance(item, dict) and isinstance(item.get("env_name"), str) and isinstance(item.get("obs"), dict) and isinstance(item["obs"].get("buckets"), list)]}
        except OSError as error:
            raise ValueError("无法读取 env.yaml") from error

    @server.tool(name="list_obs_buckets", description="列出当前环境已授权查询的桶。")
    def list_obs_buckets(environment: str) -> dict:
        try:
            return list_configured_buckets(load_environment(env_file, environment))
        except ObsConfigurationError as error:
            raise ValueError(str(error)) from error

    @server.tool(name="list_obs_objects", description="按桶、前缀和续传令牌分页列出对象。")
    def list_obs_objects(environment: str, bucket: str, prefix: str | None = None, continuation_token: str | None = None, max_keys: int = 100) -> dict:
        return _with_client(env_file, environment, lambda client, config: list_objects(client, config, bucket, prefix=prefix, continuation_token=continuation_token, max_keys=max_keys))

    @server.tool(name="get_obs_object_metadata", description="读取一个对象的元数据，不下载对象内容。")
    def get_obs_object_metadata(environment: str, bucket: str, key: str) -> dict:
        return _with_client(env_file, environment, lambda client, config: get_object_metadata(client, config, bucket, key))

    @server.tool(name="read_obs_text_object", description="受字节上限保护地读取 UTF-8 文本对象。")
    def read_obs_text_object(environment: str, bucket: str, key: str, max_bytes: int = 16_384, encoding: str = "utf-8") -> dict:
        return _with_client(env_file, environment, lambda client, config: read_text_object(client, config, bucket, key, max_bytes=max_bytes, encoding=encoding))

    return server
