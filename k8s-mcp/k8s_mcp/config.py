"""kubeconfig 路径校验和显式客户端配置加载。"""

from pathlib import Path
from dataclasses import dataclass
from typing import Any
import re
import unicodedata

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException
import yaml


class KubernetesConfigurationError(ValueError):
    """kubeconfig 不存在、不可读或不符合 Kubernetes 配置格式。"""


@dataclass(frozen=True)
class RegisteredCluster:
    """服务端预注册的 Kubernetes 集群。"""

    alias: str
    kubeconfig: Path
    context: str | None


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


def resolve_registered_cluster(clusters: dict[str, RegisteredCluster], requested: str) -> RegisteredCluster:
    """精确优先解析集群别名；仅允许唯一的业务名称模糊命中。"""

    if requested in clusters:
        return clusters[requested]
    normalized = _normalize_alias(requested)
    normalized_matches = [cluster for alias, cluster in clusters.items() if _normalize_alias(alias) == normalized]
    if len(normalized_matches) == 1:
        return normalized_matches[0]
    business_key = _business_key(requested)
    business_matches = [cluster for alias, cluster in clusters.items() if len(business_key) >= 2 and business_key in _business_key(alias)]
    if len(business_matches) == 1:
        return business_matches[0]
    if len(normalized_matches) > 1 or len(business_matches) > 1:
        raise KubernetesConfigurationError("预注册集群别名匹配不唯一，请提供正式环境名称")
    raise KubernetesConfigurationError("未找到预注册集群别名")


def load_cluster_registry(config_file: Path) -> dict[str, RegisteredCluster]:
    """读取服务端集群别名，拒绝任意调用方指定 kubeconfig。"""

    if not config_file.is_absolute() or not config_file.is_file():
        raise KubernetesConfigurationError("集群配置文件必须是存在的绝对文件路径")
    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise KubernetesConfigurationError("无法读取或解析集群配置文件") from error
    raw_clusters = data.get("clusters") if isinstance(data, dict) else None
    if not isinstance(raw_clusters, dict) or not raw_clusters:
        raise KubernetesConfigurationError("集群配置缺少 clusters 对象")
    clusters: dict[str, RegisteredCluster] = {}
    for alias, raw in raw_clusters.items():
        if not isinstance(alias, str) or not alias.strip() or not isinstance(raw, dict):
            raise KubernetesConfigurationError("集群别名和配置必须有效")
        alias = alias.strip()
        kubeconfig = raw.get("kubeconfig")
        if not isinstance(kubeconfig, str):
            raise KubernetesConfigurationError(f"集群 {alias} 缺少 kubeconfig")
        context = raw.get("context")
        if context is not None and (not isinstance(context, str) or not context.strip()):
            raise KubernetesConfigurationError(f"集群 {alias} 的 context 必须是非空字符串")
        if alias in clusters:
            raise KubernetesConfigurationError(f"集群别名重复：{alias}")
        clusters[alias] = RegisteredCluster(alias, validate_kubeconfig_path(kubeconfig), context.strip() if context else None)
    return clusters


def load_environment_registry(env_file: Path, kubeconfig_dir: Path) -> dict[str, RegisteredCluster]:
    """从共享 env.yaml 注册含 k8s_kubeconfig 的环境。"""

    if not env_file.is_absolute() or not env_file.is_file():
        raise KubernetesConfigurationError("环境配置文件必须是存在的绝对文件路径")
    if not kubeconfig_dir.is_absolute() or not kubeconfig_dir.is_dir():
        raise KubernetesConfigurationError("kubeconfig 目录必须是存在的绝对目录")
    try:
        data = yaml.safe_load(env_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise KubernetesConfigurationError("无法读取或解析环境配置文件") from error
    environments = data.get("env") if isinstance(data, dict) else None
    if not isinstance(environments, list):
        raise KubernetesConfigurationError("环境配置缺少 env 环境列表")

    clusters: dict[str, RegisteredCluster] = {}
    for item in environments:
        if not isinstance(item, dict):
            raise KubernetesConfigurationError("env 环境列表只能包含对象")
        kubeconfig_value = item.get("k8s_kubeconfig")
        if kubeconfig_value in (None, ""):
            continue
        alias = item.get("env_name")
        if not isinstance(alias, str) or not alias.strip():
            raise KubernetesConfigurationError("包含 k8s_kubeconfig 的环境必须提供 env_name")
        alias = alias.strip()
        if not isinstance(kubeconfig_value, str):
            raise KubernetesConfigurationError(f"环境 {alias} 的 k8s_kubeconfig 必须是字符串")
        filename = Path(kubeconfig_value.replace("\\", "/")).name
        if not filename or filename in {".", ".."}:
            raise KubernetesConfigurationError(f"环境 {alias} 的 k8s_kubeconfig 无效")
        kubeconfig = validate_kubeconfig_path(kubeconfig_dir / filename)
        if alias in clusters:
            raise KubernetesConfigurationError(f"集群别名重复：{alias}")
        context = item.get("k8s_context")
        if context is not None and (not isinstance(context, str) or not context.strip()):
            raise KubernetesConfigurationError(f"环境 {alias} 的 k8s_context 必须是非空字符串")
        clusters[alias] = RegisteredCluster(alias, kubeconfig, context.strip() if context else None)
    if not clusters:
        raise KubernetesConfigurationError("环境配置中未找到 k8s_kubeconfig")
    return clusters


def validate_kubeconfig_path(value: str) -> Path:
    """校验调用方传入的 kubeconfig 绝对文件路径，拒绝默认配置回退。"""

    path = Path(value)
    if not path.is_absolute():
        raise KubernetesConfigurationError("kubeconfig 必须是绝对文件路径")
    if not path.is_file():
        raise KubernetesConfigurationError("kubeconfig 文件不存在或不是普通文件")
    return path


def create_api_client(kubeconfig: str, context: str | None = None) -> tuple[client.ApiClient, str]:
    """根据显式 kubeconfig 创建 API 客户端，并返回实际使用的 context 名称。"""

    path = validate_kubeconfig_path(kubeconfig)
    try:
        contexts, active_context = config.list_kube_config_contexts(config_file=str(path))
        if not contexts:
            raise KubernetesConfigurationError("kubeconfig 未包含可用 context")
        selected_context = context or (active_context or {}).get("name")
        if not selected_context:
            raise KubernetesConfigurationError("kubeconfig 未指定 current-context，请传入 context")
        if selected_context not in {item["name"] for item in contexts}:
            raise KubernetesConfigurationError(f"kubeconfig 中不存在 context：{selected_context}")

        configuration = client.Configuration()
        config.load_kube_config(
            config_file=str(path),
            context=selected_context,
            client_configuration=configuration,
            persist_config=False,
        )
        return client.ApiClient(configuration=configuration), selected_context
    except KubernetesConfigurationError:
        raise
    except (ConfigException, OSError, ValueError) as error:
        raise KubernetesConfigurationError("无法加载 kubeconfig，请检查文件格式和 context") from error
