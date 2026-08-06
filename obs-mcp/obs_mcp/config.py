from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class ObsConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ObsEnvironment:
    name: str
    endpoint: str
    access_key_id: str
    secret_access_key: str
    region: str
    buckets: tuple[str, ...]
    use_ssl: bool
    connect_timeout_seconds: float
    read_timeout_seconds: float


def load_environment(env_file: Path, environment: str) -> ObsEnvironment:
    try:
        data = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
    except OSError as error:
        raise ObsConfigurationError("无法读取 env.yaml") from error
    items = data.get("env") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ObsConfigurationError("env.yaml 缺少合法的 env 环境列表")
    item = next((value for value in items if isinstance(value, dict) and value.get("env_name") == environment), None)
    if item is None:
        raise ObsConfigurationError(f"未找到环境：{environment}")
    obs = item.get("obs")
    if not isinstance(obs, dict):
        raise ObsConfigurationError(f"环境 {environment} 未配置 obs")
    values = {key: obs.get(key) for key in ("endpoint", "access_key_id", "secret_access_key", "region")}
    if any(not isinstance(value, str) or not value.strip() for value in values.values()):
        raise ObsConfigurationError("obs.endpoint、access_key_id、secret_access_key 和 region 必须为非空字符串")
    buckets = obs.get("buckets")
    if not isinstance(buckets, list) or not buckets or any(not isinstance(bucket, str) or not bucket.strip() for bucket in buckets):
        raise ObsConfigurationError("obs.buckets 必须为至少包含一个桶名的列表")
    endpoint = values["endpoint"].strip().rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        raise ObsConfigurationError("obs.endpoint 必须以 http:// 或 https:// 开头")
    def timeout(key: str, default: float) -> float:
        value = obs.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ObsConfigurationError(f"obs.{key} 必须是正数")
        return float(value)
    return ObsEnvironment(item["env_name"], endpoint, values["access_key_id"].strip(), values["secret_access_key"].strip(), values["region"].strip(), tuple(bucket.strip() for bucket in buckets), endpoint.startswith("https://"), timeout("connect_timeout_seconds", 5), timeout("read_timeout_seconds", 15))
