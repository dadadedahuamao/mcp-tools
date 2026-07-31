"""kubeconfig 路径校验和显式客户端配置加载。"""

from pathlib import Path

from kubernetes import client, config
from kubernetes.config.config_exception import ConfigException


class KubernetesConfigurationError(ValueError):
    """kubeconfig 不存在、不可读或不符合 Kubernetes 配置格式。"""


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

