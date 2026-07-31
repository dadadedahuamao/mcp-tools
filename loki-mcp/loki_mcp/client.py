"""通过 Grafana 数据源代理执行只读 Loki 查询。"""

import base64
import json
import ssl
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from loki_mcp.config import LokiEnvironment


MAX_LIMIT = 2_000


class LokiQueryError(ValueError):
    """Loki 查询参数不合法。"""


def append_contains_filter(query: str, contains: str | None) -> str:
    """在默认 LogQL 后追加安全转义的文本包含过滤条件。"""

    if not contains:
        return query

    escaped = contains.replace("\\", "\\\\").replace('"', '\\"')
    return f'{query} |= "{escaped}"'


def _to_nanoseconds(value: datetime) -> int:
    """将带时区的时间转换为 Loki 使用的 Unix 纳秒时间戳。"""

    if value.tzinfo is None:
        raise LokiQueryError("start 和 end 必须包含时区")

    utc_value = value.astimezone(timezone.utc)
    return int(utc_value.timestamp()) * 1_000_000_000 + utc_value.microsecond * 1_000


def build_range_request(
    config: LokiEnvironment,
    start: datetime,
    end: datetime,
    *,
    limit: int,
    contains: str | None,
) -> Request:
    """构造只读 Loki `query_range` 请求，不发送网络请求。"""

    if not 1 <= limit <= MAX_LIMIT:
        raise LokiQueryError(f"limit 必须在 1 到 {MAX_LIMIT} 之间")
    if start >= end:
        raise LokiQueryError("start 必须早于 end")

    parameters = urlencode(
        {
            "query": append_contains_filter(config.query, contains),
            "start": str(_to_nanoseconds(start)),
            "end": str(_to_nanoseconds(end)),
            "limit": str(limit),
            "direction": "backward",
        }
    )
    endpoint = (
        f"{config.base_url}/api/datasources/proxy/uid/"
        f"{config.datasource_uid}/loki/api/v1/query_range?{parameters}"
    )
    credentials = base64.b64encode(
        f"{config.username}:{config.password}".encode("utf-8")
    ).decode("ascii")
    return Request(endpoint, headers={"Authorization": f"Basic {credentials}"})


def _ssl_context(config: LokiEnvironment) -> ssl.SSLContext:
    """保留证书校验，仅为 UAT Grafana 启用必要的旧协商兼容性。"""

    context = ssl.create_default_context()
    hostname = urlparse(config.base_url).hostname or ""
    if hostname.endswith("mesu.xcmg.com"):
        context.options |= ssl.OP_LEGACY_SERVER_CONNECT
    return context


def query_logs(
    config: LokiEnvironment,
    start: datetime,
    end: datetime,
    *,
    limit: int = 200,
    contains: str | None = None,
    opener: Callable = urlopen,
) -> dict:
    """执行受限的只读范围查询，并返回适合 MCP 传输的紧凑日志。"""

    request = build_range_request(config, start, end, limit=limit, contains=contains)
    try:
        with opener(request, context=_ssl_context(config), timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:
        raise LokiQueryError(f"Loki 查询失败：{error.__class__.__name__}") from error

    data = payload.get("data") or {}
    streams = data.get("result") or []
    entries = []
    for stream in streams:
        labels = stream.get("stream") or {}
        for value in stream.get("values") or []:
            timestamp_ns, line = value[:2]
            timestamp = datetime.fromtimestamp(
                int(timestamp_ns) / 1_000_000_000, tz=timezone.utc
            ).isoformat()
            entries.append({"timestamp": timestamp, "labels": labels, "line": line})

    return {
        "status": payload.get("status"),
        "result_type": data.get("resultType"),
        "stream_count": len(streams),
        "returned_entry_count": len(entries),
        "entries": entries,
    }
