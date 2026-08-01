"""读取和校验共享 env.yaml 内的 Redis 配置。"""

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata
from typing import Any, Literal

import yaml

class RedisConfigurationError(ValueError):
    """Redis 环境配置缺失或不合法。"""


@dataclass(frozen=True)
class RedisNode:
    host: str
    port: int


@dataclass(frozen=True)
class RedisEnvironment:
    name: str
    mode: Literal["standalone", "sentinel", "cluster"]
    username: str | None
    password: str | None
    tls: bool
    socket_timeout_seconds: float
    connect_timeout_seconds: float
    database: int
    host: str | None = None
    port: int | None = None
    master_name: str | None = None
    sentinels: tuple[RedisNode, ...] = ()
    startup_nodes: tuple[RedisNode, ...] = ()


_GENERIC_ALIAS_TERMS = ("kubernetes", "k8s", "环境", "集群", "一期", "二期", "uat", "生产", "测试", "test")


def _normalize_alias(value: str) -> str:
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", value).casefold())


def _business_key(value: str) -> str:
    normalized = _normalize_alias(value)
    for term in _GENERIC_ALIAS_TERMS:
        normalized = normalized.replace(term, "")
    return normalized


def _resolve_environment(environments: list[dict[str, Any]], requested: str) -> dict[str, Any]:
    valid = [item for item in environments if isinstance(item.get("env_name"), str)]
    exact = [item for item in valid if item["env_name"] == requested]
    normalized = _normalize_alias(requested)
    normalized_matches = [item for item in valid if _normalize_alias(item["env_name"]) == normalized]
    business_key = _business_key(requested)
    business_matches = [item for item in valid if len(business_key) >= 2 and business_key in _business_key(item["env_name"])]
    for matches in (exact, normalized_matches, business_matches):
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise RedisConfigurationError("环境名称匹配不唯一，请提供正式环境名称")
    raise RedisConfigurationError(f"未找到环境：{requested}")


def _number(value: Any, label: str, *, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise RedisConfigurationError(f"{label} 必须是正数")
    return float(value)


def _port(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise RedisConfigurationError(f"{label} 必须是 1 到 65535 的整数")
    return value


def _nodes(value: Any, label: str) -> tuple[RedisNode, ...]:
    if not isinstance(value, list) or not value:
        raise RedisConfigurationError(f"{label} 必须是至少包含一个节点的列表")
    result: list[RedisNode] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("host"), str) or not item["host"].strip():
            raise RedisConfigurationError(f"{label} 节点必须包含 host")
        result.append(RedisNode(item["host"].strip(), _port(item.get("port", 6379), f"{label}.port")))
    return tuple(result)


def load_environment(env_file: Path, environment: str) -> RedisEnvironment:
    """从共享 env.yaml 中定位环境并读取对应 Redis 配置。"""

    try:
        data = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
    except OSError as error:
        raise RedisConfigurationError("无法读取 env.yaml") from error
    environments = data.get("env") if isinstance(data, dict) else None
    if not isinstance(environments, list) or not all(isinstance(item, dict) for item in environments):
        raise RedisConfigurationError("env.yaml 缺少合法的 env 环境列表")
    item = _resolve_environment(environments, environment)
    redis = item.get("redis")
    if not isinstance(redis, dict):
        raise RedisConfigurationError(f"环境 {environment} 未配置 Redis")
    mode = redis.get("mode")
    if mode not in {"standalone", "sentinel", "cluster"}:
        raise RedisConfigurationError("redis.mode 必须是 standalone、sentinel 或 cluster")
    password = redis.get("password")
    if password is not None and not isinstance(password, str):
        raise RedisConfigurationError("redis.password 必须是字符串")
    username = redis.get("username")
    if username is not None and not isinstance(username, str):
        raise RedisConfigurationError("redis.username 必须是字符串")
    database = redis.get("database", 0)
    if isinstance(database, bool) or not isinstance(database, int) or database < 0:
        raise RedisConfigurationError("redis.database 必须是非负整数")
    common = dict(
        name=str(item["env_name"]), mode=mode, username=username or None, password=password or None,
        tls=bool(redis.get("tls", False)), socket_timeout_seconds=_number(redis.get("socket_timeout_seconds"), "socket_timeout_seconds", default=5),
        connect_timeout_seconds=_number(redis.get("connect_timeout_seconds"), "connect_timeout_seconds", default=5), database=database,
    )
    if mode == "standalone":
        host = redis.get("host")
        if not isinstance(host, str) or not host.strip():
            raise RedisConfigurationError("standalone 必须配置 host")
        return RedisEnvironment(**common, host=host.strip(), port=_port(redis.get("port", 6379), "port"))
    if mode == "sentinel":
        master_name = redis.get("master_name")
        if not isinstance(master_name, str) or not master_name.strip():
            raise RedisConfigurationError("sentinel 必须配置 master_name")
        return RedisEnvironment(**common, master_name=master_name.strip(), sentinels=_nodes(redis.get("sentinels"), "sentinels"))
    if database != 0:
        raise RedisConfigurationError("Redis Cluster 仅支持 DB 0，请将 redis.database 设为 0 或省略")
    return RedisEnvironment(**common, startup_nodes=_nodes(redis.get("startup_nodes"), "startup_nodes"))
