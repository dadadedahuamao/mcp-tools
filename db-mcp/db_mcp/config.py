"""集中式数据源配置的加载与校验。"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any, Mapping
import re
import unicodedata

import yaml


class DatabaseConfigurationError(ValueError):
    """数据源配置缺失、不合法或不安全。"""


@dataclass(frozen=True)
class QueryLimits:
    """每个数据源可覆盖的只读查询限制。"""

    max_rows: int = 1_000
    timeout_seconds: int = 30
    max_response_bytes: int = 1_000_000
    connect_timeout_seconds: int = 10


@dataclass(frozen=True)
class DatabaseSource:
    """已经验证的数据源；敏感字段不会出现在对象表示中。"""

    alias: str
    dialect: str
    host: str
    port: int
    database: str
    username: str
    password_env: str | None
    password: str = field(repr=False)
    default_schema: str
    allowed_schemas: frozenset[str]
    tls: Mapping[str, Any] | bool | None
    query_limits: QueryLimits

    def safe_summary(self) -> dict[str, object]:
        """返回可以安全提供给 MCP 调用者的数据源摘要。"""

        return {
            "alias": self.alias,
            "dialect": self.dialect,
            "default_schema": self.default_schema,
            "allowed_schemas": sorted(self.allowed_schemas),
        }


@dataclass(frozen=True)
class DatabaseConfig:
    """按别名索引的预注册数据源集合。"""

    sources: Mapping[str, DatabaseSource]


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


def load_config(config_file: Path) -> DatabaseConfig:
    """读取绝对路径 YAML 文件，并在启动前完成所有安全校验。"""

    if not config_file.is_absolute():
        raise DatabaseConfigurationError("配置文件必须使用绝对路径")
    if not config_file.is_file():
        raise DatabaseConfigurationError("配置文件不存在或不是常规文件")

    try:
        raw_config = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise DatabaseConfigurationError("无法读取或解析配置文件") from error

    if not isinstance(raw_config, dict):
        raise DatabaseConfigurationError("配置根节点必须是对象")
    datasources = raw_config.get("datasources")
    if datasources is None:
        datasources = _datasources_from_unified_env(raw_config)
    if not isinstance(datasources, dict) or not datasources:
        raise DatabaseConfigurationError("配置缺少 datasources 对象或 env 环境列表")

    sources: dict[str, DatabaseSource] = {}
    for alias, raw_source in datasources.items():
        if not isinstance(alias, str) or not alias.strip():
            raise DatabaseConfigurationError("数据源别名必须是非空字符串")
        if not isinstance(raw_source, dict):
            raise DatabaseConfigurationError(f"数据源 {alias} 必须是对象")
        sources[alias] = _parse_source(alias, raw_source)
    return DatabaseConfig(sources=sources)


def _datasources_from_unified_env(raw_config: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """将共享 env.yaml 中的数据库字段转换为内部数据源格式。"""

    environments = raw_config.get("env")
    if not isinstance(environments, list):
        return {}

    dialects = {"postgres": "postgresql", "postgresql": "postgresql", "mysql": "mysql", "oracle": "oracle"}
    sources: dict[str, dict[str, Any]] = {}
    for item in environments:
        if not isinstance(item, dict):
            raise DatabaseConfigurationError("env 环境列表只能包含对象")
        alias = item.get("db_connect_name")
        if not isinstance(alias, str) or not alias.strip():
            raise DatabaseConfigurationError("env 环境缺少有效的 db_connect_name")
        if alias in sources:
            raise DatabaseConfigurationError(f"数据源别名重复：{alias}")
        raw_dialect = item.get("db_type")
        dialect = dialects.get(raw_dialect.lower()) if isinstance(raw_dialect, str) else None
        if dialect is None:
            raise DatabaseConfigurationError(f"数据源 {alias} 使用了不支持的 db_type")
        username = item.get("db_user")
        schema = item.get("schema") or username
        port = item.get("db_port")
        if isinstance(port, str) and port.strip().isdigit():
            port = int(port.strip())
        source: dict[str, Any] = {
            "dialect": dialect,
            "host": item.get("db_host"),
            "port": port,
            "username": username,
            "password": item.get("db_passwd"),
            "default_schema": schema,
            "allowed_schemas": [schema] if isinstance(schema, str) and schema.strip() else [],
        }
        source["service_name" if dialect == "oracle" else "database"] = item.get("db_name")
        sources[alias.strip()] = source
    return sources


def load_source(config: DatabaseConfig, alias: str) -> DatabaseSource:
    """按已预注册的别名取得数据源，允许唯一业务名称匹配。"""

    if alias in config.sources:
        return config.sources[alias]
    normalized = _normalize_alias(alias)
    normalized_matches = [source for registered, source in config.sources.items() if _normalize_alias(registered) == normalized]
    if len(normalized_matches) == 1:
        return normalized_matches[0]
    business_key = _business_key(alias)
    business_matches = [source for registered, source in config.sources.items() if len(business_key) >= 2 and business_key in _business_key(registered)]
    if len(normalized_matches) > 1 or len(business_matches) > 1:
        raise DatabaseConfigurationError("预注册数据源别名匹配不唯一，请提供正式别名")
    raise DatabaseConfigurationError(f"未找到预注册数据源：{alias}")


def _parse_source(alias: str, raw: Mapping[str, Any]) -> DatabaseSource:
    dialect = _required_string(raw, "dialect", alias).lower()
    if dialect not in {"mysql", "postgresql", "oracle"}:
        raise DatabaseConfigurationError(f"数据源 {alias} 使用了不支持的数据库类型")

    host = _required_string(raw, "host", alias)
    port = raw.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise DatabaseConfigurationError(f"数据源 {alias} 的 port 必须是 1 到 65535 的整数")

    database_key = "service_name" if dialect == "oracle" else "database"
    database = _required_string(raw, database_key, alias)
    username = _required_string(raw, "username", alias)
    plain_password = raw.get("password")
    raw_password_env = raw.get("password_env")
    if isinstance(plain_password, str) and plain_password:
        if raw_password_env is not None:
            raise DatabaseConfigurationError(f"数据源 {alias} 的 password 与 password_env 只能二选一")
        password_env = None
        password = plain_password
    else:
        password_env = _required_string(raw, "password_env", alias)
        password = os.environ.get(password_env)
        if not password:
            raise DatabaseConfigurationError(f"数据源 {alias} 的密码环境变量未设置：{password_env}")

    default_schema = _required_string(raw, "default_schema", alias)
    allowed_raw = raw.get("allowed_schemas")
    if not isinstance(allowed_raw, list) or not allowed_raw:
        raise DatabaseConfigurationError(f"数据源 {alias} 的 allowed_schemas 必须是非空列表")
    if any(not isinstance(item, str) or not item.strip() for item in allowed_raw):
        raise DatabaseConfigurationError(f"数据源 {alias} 的 allowed_schemas 只能包含非空字符串")
    allowed_schemas = frozenset(item.strip() for item in allowed_raw)
    if default_schema not in allowed_schemas:
        raise DatabaseConfigurationError(
            f"数据源 {alias} 的 default_schema 必须包含在 allowed_schemas 中"
        )

    tls = raw.get("tls")
    if tls is not None and not isinstance(tls, (bool, dict)):
        raise DatabaseConfigurationError(f"数据源 {alias} 的 tls 必须是布尔值或对象")
    return DatabaseSource(
        alias=alias,
        dialect=dialect,
        host=host,
        port=port,
        database=database,
        username=username,
        password_env=password_env,
        password=password,
        default_schema=default_schema,
        allowed_schemas=allowed_schemas,
        tls=tls,
        query_limits=_parse_limits(alias, raw.get("query_limits")),
    )


def _required_string(raw: Mapping[str, Any], field_name: str, alias: str) -> str:
    value = raw.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise DatabaseConfigurationError(f"数据源 {alias} 缺少有效的 {field_name}")
    return value.strip()


def _parse_limits(alias: str, raw_limits: Any) -> QueryLimits:
    if raw_limits is None:
        return QueryLimits()
    if not isinstance(raw_limits, dict):
        raise DatabaseConfigurationError(f"数据源 {alias} 的 query_limits 必须是对象")
    defaults = QueryLimits()
    values: dict[str, int] = {}
    for field_name in (
        "max_rows",
        "timeout_seconds",
        "max_response_bytes",
        "connect_timeout_seconds",
    ):
        value = raw_limits.get(field_name, getattr(defaults, field_name))
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise DatabaseConfigurationError(
                f"数据源 {alias} 的 query_limits.{field_name} 必须为正整数"
            )
        values[field_name] = value
    return QueryLimits(**values)
