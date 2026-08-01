"""读取并校验项目环境中的 Loki 配置。"""

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata
from typing import Any

import yaml


class LokiConfigurationError(ValueError):
    """Loki 环境配置缺失或不合法。"""


@dataclass(frozen=True)
class LokiEnvironment:
    """执行只读 Loki 查询所需的单一环境配置。"""

    name: str
    base_url: str
    datasource_uid: str
    username: str
    password: str
    query: str


_GENERIC_ALIAS_TERMS = ("kubernetes", "k8s", "环境", "集群", "一期", "二期", "uat", "生产", "测试", "test")


def _normalize_alias(value: str) -> str:
    """规范化别名，消除全半角、大小写、空白和分隔符差异。"""

    return re.sub(r"[^\\w]", "", unicodedata.normalize("NFKC", value).casefold())


def _business_key(value: str) -> str:
    """去除环境通用词，仅保留用于唯一识别业务域的文本。"""

    normalized = _normalize_alias(value)
    for term in _GENERIC_ALIAS_TERMS:
        normalized = normalized.replace(term, "")
    return normalized


def resolve_environment(environments: list[dict[str, Any]], requested: str) -> dict[str, Any]:
    """精确优先解析 Loki 环境；仅允许唯一的业务名称模糊命中。"""

    valid = [item for item in environments if isinstance(item.get("env_name"), str)]
    exact_matches = [item for item in valid if item["env_name"] == requested]
    if len(exact_matches) == 1:
        return exact_matches[0]
    normalized = _normalize_alias(requested)
    normalized_matches = [item for item in valid if _normalize_alias(item["env_name"]) == normalized]
    if len(normalized_matches) == 1:
        return normalized_matches[0]
    business_key = _business_key(requested)
    business_matches = [item for item in valid if len(business_key) >= 2 and business_key in _business_key(item["env_name"])]
    if len(business_matches) == 1:
        return business_matches[0]
    if len(exact_matches) > 1 or len(normalized_matches) > 1 or len(business_matches) > 1:
        raise LokiConfigurationError("环境名称匹配不唯一，请提供正式环境名称")
    raise LokiConfigurationError(f"未找到环境：{requested}")


def load_environment(env_file: Path, environment: str) -> LokiEnvironment:
    """按环境名称解析并读取 `env.yaml` 中的 Loki 配置。"""

    data = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
    environments = data.get("env")
    if not isinstance(environments, list):
        raise LokiConfigurationError("env.yaml 缺少 env 环境列表")

    if not all(isinstance(item, dict) for item in environments):
        raise LokiConfigurationError("env.yaml 的 env 环境列表只能包含对象")
    match = resolve_environment(environments, environment)

    required_fields = {
        "loki_url": "Grafana 地址",
        "loki_datasource_uid": "Loki 数据源 UID",
        "loki_user_name": "Loki 用户名",
        "loki_passwd": "Loki 密码",
        "loki_query": "默认 LogQL",
    }
    missing = [label for field, label in required_fields.items() if not match.get(field)]
    if missing:
        raise LokiConfigurationError(
            f"环境 {environment} 缺少 Loki 配置：{', '.join(missing)}"
        )

    return LokiEnvironment(
        name=str(match["env_name"]),
        base_url=str(match["loki_url"]).rstrip("/"),
        datasource_uid=str(match["loki_datasource_uid"]),
        username=str(match["loki_user_name"]),
        password=str(match["loki_passwd"]),
        query=str(match["loki_query"]),
    )
