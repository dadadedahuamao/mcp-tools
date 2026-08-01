"""受限的 Redis 只读诊断操作。"""

from typing import Any

from redis_mcp.config import RedisEnvironment

MAX_SCAN_COUNT = 200
MAX_VALUE_BYTES = 2048
MAX_LIST_ITEMS = 100


class RedisQueryError(ValueError):
    pass


def _decode(value: Any) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)


def create_client(config: RedisEnvironment) -> Any:
    try:
        import redis
        from redis.cluster import ClusterNode
        from redis.sentinel import Sentinel
        options = {"decode_responses": False, "socket_timeout": config.socket_timeout_seconds,
                   "socket_connect_timeout": config.connect_timeout_seconds, "ssl": config.tls}
        if config.password:
            options["password"] = config.password
            if config.username:
                options["username"] = config.username
        if config.mode == "standalone":
            return redis.Redis(host=config.host, port=config.port, db=config.database, **options)
        if config.mode == "sentinel":
            sentinel = Sentinel([(node.host, node.port) for node in config.sentinels], **options)
            return sentinel.master_for(config.master_name, db=config.database, **options)
        return redis.RedisCluster(startup_nodes=[ClusterNode(node.host, node.port) for node in config.startup_nodes], **options)
    except Exception as error:
        raise RedisQueryError("Redis 连接初始化失败") from error


def scan_keys(client: Any, *, cursor: int, match: str | None, count: int = 100) -> dict:
    try:
        count = min(max(count, 1), MAX_SCAN_COUNT)
        next_cursor, keys = client.scan(cursor=cursor, match=match, count=count)
        return {"next_cursor": int(next_cursor), "keys": [_decode(key) for key in keys[:MAX_SCAN_COUNT]]}
    except Exception as error:
        raise RedisQueryError("Redis 扫描失败") from error


def inspect_key(client: Any, key: str) -> dict:
    try:
        kind = _decode(client.type(key))
        result = {"key": key, "type": kind, "ttl_seconds": client.ttl(key), "memory_bytes": client.memory_usage(key)}
        if kind == "none": return result
        if kind == "string":
            raw = client.get(key) or b""; data = _decode(raw[:MAX_VALUE_BYTES])
            result["value"] = {"data": data, "truncated": len(raw) > MAX_VALUE_BYTES}
        elif kind == "list": result["value"] = {"items": [_decode(x) for x in client.lrange(key, 0, MAX_LIST_ITEMS - 1)], "truncated": client.llen(key) > MAX_LIST_ITEMS}
        elif kind == "hash":
            cursor, pairs = client.hscan(key, count=MAX_LIST_ITEMS); result["value"] = {"items": {_decode(k): _decode(v) for k, v in pairs.items()}, "truncated": bool(cursor)}
        else: result["value"] = {"summary": "为避免大键响应，未展开此类型内容"}
        return result
    except Exception as error:
        raise RedisQueryError("Redis 键查询失败") from error


def get_overview(client: Any, mode: str) -> dict:
    try:
        info = client.info()
        allowed = ("redis_version", "uptime_in_seconds", "connected_clients", "used_memory", "used_memory_human", "maxmemory", "total_commands_processed", "instantaneous_ops_per_sec", "keyspace_hits", "keyspace_misses", "role")
        return {"mode": mode, "info": {key: info[key] for key in allowed if key in info}, "keyspace": info.get("db0", {})}
    except Exception as error: raise RedisQueryError("Redis 概览查询失败") from error


def get_slowlog(client: Any, limit: int = 50) -> dict:
    try: return {"entries": client.slowlog_get(min(max(limit, 1), 200))}
    except Exception as error: raise RedisQueryError("Redis 慢日志查询失败") from error


def get_clients(client: Any, limit: int = 50) -> dict:
    try:
        entries = client.client_list()[:min(max(limit, 1), 200)]
        return {"connected_clients": client.info("clients").get("connected_clients"), "clients": [{k: item.get(k) for k in ("id", "addr", "age", "idle", "flags", "db", "cmd")} for item in entries]}
    except Exception as error: raise RedisQueryError("Redis 客户端查询失败") from error


def get_command_stats(client: Any) -> dict:
    try: return {"commands": client.info("commandstats")}
    except Exception as error: raise RedisQueryError("Redis 命令统计查询失败") from error
