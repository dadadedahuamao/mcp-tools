"""读取和校验共享 env.yaml 内的 Elasticsearch 配置。"""

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata
from typing import Any

import yaml


class ElasticsearchConfigurationError(ValueError):
    """Elasticsearch 环境配置缺失或不合法。"""


@dataclass(frozen=True)
class ElasticsearchEnvironment:
    """已经验证的 Elasticsearch 环境配置。"""

    name: str
    hosts: tuple[str, ...]
    username: str | None
    password: str | None
    index_allowlist: frozenset[str]
    timeout_seconds: int
    max_result_window: int
    max_response_bytes: int

    def safe_summary(self) -> dict[str, object]:
        """返回可以安全提供给 MCP 调用者的数据源摘要。"""
        return {
            "name": self.name,
            "hosts": list(self.hosts),
            "index_allowlist": sorted(self.index_allowlist),
            "timeout_seconds": self.timeout_seconds,
            "max_result_window": self.max_result_window,
        }


_GENERIC_ALIAS_TERMS = ("kubernetes", "k8s", "环境", "集群", "一期", "二期", "uat", "生产", "测试", "test")


def _normalize_alias(value: str) -> str:
    """规范化别名，消除全半角、大小写、空白和分隔符差异。"""
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", value).casefold())


def _business_key(value: str) -> str:
    """去除环境通用词，仅保留用于唯一识别业务域的文本。"""
    normalized = _normalize_alias(value)
    for term in _GENERIC_ALIAS_TERMS:
        normalized = normalized.replace(term, "")
    return normalized


def _resolve_environment(environments: list[dict[str, Any]], requested: str) -> dict[str, Any]:
    """根据名称定位环境，支持精确匹配、归一化匹配和业务键子串匹配。"""
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
            raise ElasticsearchConfigurationError("环境名称匹配不唯一，请提供正式环境名称")
    raise ElasticsearchConfigurationError(f"未找到环境：{requested}")


def _hosts(value: Any, label: str) -> tuple[str, ...]:
    """校验并返回主机列表。"""
    if not isinstance(value, list) or not value:
        raise ElasticsearchConfigurationError(f"{label} 必须是至少包含一个节点的列表")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ElasticsearchConfigurationError(f"{label} 节点必须是非空字符串")
        result.append(item.strip())
    return tuple(result)


def _index_allowlist(value: Any, label: str) -> frozenset[str]:
    """校验并返回索引白名单。"""
    if not isinstance(value, list) or not value:
        raise ElasticsearchConfigurationError(f"{label} 必须是至少包含一个索引模式的列表")
    result: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ElasticsearchConfigurationError(f"{label} 索引模式必须是非空字符串")
        result.add(item.strip())
    return frozenset(result)


def _number(value: Any, label: str, *, default: int) -> int:
    """校验并返回正整数。"""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ElasticsearchConfigurationError(f"{label} 必须是正整数")
    return value


def load_environment(env_file: Path, environment: str) -> ElasticsearchEnvironment:
    """从共享 env.yaml 中定位环境并读取对应 Elasticsearch 配置。"""

    try:
        data = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
    except OSError as error:
        raise ElasticsearchConfigurationError("无法读取 env.yaml") from error

    environments = data.get("env") if isinstance(data, dict) else None
    if not isinstance(environments, list) or not all(isinstance(item, dict) for item in environments):
        raise ElasticsearchConfigurationError("env.yaml 缺少合法的 env 环境列表")

    item = _resolve_environment(environments, environment)
    es = item.get("es")
    if not isinstance(es, dict):
        raise ElasticsearchConfigurationError(f"环境 {environment} 未配置 Elasticsearch")

    hosts = _hosts(es.get("hosts"), "es.hosts")
    username = es.get("username")
    if username is not None and not isinstance(username, str):
        raise ElasticsearchConfigurationError("es.username 必须是字符串")
    password = es.get("password")
    if password is not None and not isinstance(password, str):
        raise ElasticsearchConfigurationError("es.password 必须是字符串")
    index_allowlist = _index_allowlist(es.get("index_allowlist"), "es.index_allowlist")

    return ElasticsearchEnvironment(
        name=str(item["env_name"]),
        hosts=hosts,
        username=username or None,
        password=password or None,
        index_allowlist=index_allowlist,
        timeout_seconds=_number(es.get("timeout_seconds"), "timeout_seconds", default=30),
        max_result_window=_number(es.get("max_result_window"), "max_result_window", default=10000),
        max_response_bytes=_number(es.get("max_response_bytes"), "max_response_bytes", default=1048576),
    )
