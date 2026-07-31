"""读取并校验项目环境中的 Loki 配置。"""

from dataclasses import dataclass
from pathlib import Path

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


def load_environment(env_file: Path, environment: str) -> LokiEnvironment:
    """按环境名称精确读取 `env.yaml` 中的 Loki 配置。"""

    data = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
    environments = data.get("env")
    if not isinstance(environments, list):
        raise LokiConfigurationError("env.yaml 缺少 env 环境列表")

    match = next(
        (
            item
            for item in environments
            if isinstance(item, dict) and item.get("env_name") == environment
        ),
        None,
    )
    if match is None:
        raise LokiConfigurationError(f"未找到环境：{environment}")

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
        name=environment,
        base_url=str(match["loki_url"]).rstrip("/"),
        datasource_uid=str(match["loki_datasource_uid"]),
        username=str(match["loki_user_name"]),
        password=str(match["loki_passwd"]),
        query=str(match["loki_query"]),
    )
