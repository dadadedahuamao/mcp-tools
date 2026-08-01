"""使用 Kubernetes Python SDK 执行受限只读查询。"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Any

from kubernetes import client
from kubernetes.client.exceptions import ApiException

from k8s_mcp.config import KubernetesConfigurationError, create_api_client


DEFAULT_LIMIT = 100
MAX_LIMIT = 500
DEFAULT_TAIL_LINES = 200
MAX_TAIL_LINES = 2_000
DEFAULT_LOG_BYTES = 65_536
MAX_LOG_BYTES = 262_144
# 中国标准时间固定为 UTC+08:00，避免 Windows 单文件程序依赖 IANA 时区数据库。
SHANGHAI_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _safe_path_component(value: str, label: str) -> str:
    """将资源标识转换为固定下载目录内的单个安全文件名片段。"""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 不能为空")
    component = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._")
    if not component:
        raise ValueError(f"{label} 不包含可用文件名字符")
    return component[:80]


class KubernetesQueryError(ValueError):
    """Kubernetes API 的安全、可展示查询错误。"""


def _positive_limit(value: int, maximum: int, label: str) -> int:
    """校验所有列表和日志范围，避免一次请求返回过量数据。"""

    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise ValueError(f"{label} 必须是 1 到 {maximum} 的整数")
    return value


def _metadata(resource: Any) -> dict[str, Any]:
    """提取所有资源共用的非敏感元数据。"""

    metadata = resource.metadata
    return {
        "name": metadata.name,
        "namespace": getattr(metadata, "namespace", None),
        "creation_timestamp": _as_text(getattr(metadata, "creation_timestamp", None)),
        "labels": getattr(metadata, "labels", None) or {},
    }


def _as_text(value: Any) -> str | None:
    """将 SDK 时间对象统一转换为上海时区的秒级可展示文本。"""

    if isinstance(value, datetime):
        localized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        return localized.astimezone(SHANGHAI_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=SHANGHAI_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    return str(value) if value else None


def _event_summary(item: Any) -> dict[str, Any]:
    """兼容 Events v1 与 CoreV1 Event 的安全摘要字段。"""

    regarding = getattr(item, "regarding", None) or getattr(item, "involved_object", None)
    event_time = next(
        (
            _as_text(value)
            for value in (
                getattr(item, "event_time", None),
                getattr(item, "last_timestamp", None),
                getattr(item, "first_timestamp", None),
                getattr(item.metadata, "creation_timestamp", None),
            )
            if _as_text(value) is not None
        ),
        None,
    )
    return {
        **_metadata(item),
        "type": getattr(item, "type", None),
        "reason": getattr(item, "reason", None),
        "note": getattr(item, "note", None) or getattr(item, "message", None),
        "regarding": {"kind": getattr(regarding, "kind", None), "name": getattr(regarding, "name", None)},
        "event_time": event_time,
    }


def _api_error(error: ApiException) -> KubernetesQueryError:
    """转换 SDK 异常；不返回 HTTP 请求、响应体或认证内容。"""

    if error.status == 401:
        return KubernetesQueryError("集群认证失败，请检查 kubeconfig 凭据")
    if error.status == 403:
        return KubernetesQueryError("当前凭据没有查询该 Kubernetes 资源的权限")
    if error.status == 404:
        return KubernetesQueryError("未找到指定的 Kubernetes 资源")
    return KubernetesQueryError(f"Kubernetes API 查询失败（HTTP {error.status or '未知'}）")


@dataclass
class KubernetesReader:
    """将 SDK 的只读 API 封装为稳定、裁剪后的查询结果。"""

    api_client: client.ApiClient
    context: str

    @classmethod
    def from_kubeconfig(cls, kubeconfig: str, context: str | None = None) -> "KubernetesReader":
        """仅从调用参数中指定的 kubeconfig 创建读取器。"""

        api_client, selected_context = create_api_client(kubeconfig, context)
        return cls(api_client=api_client, context=selected_context)

    def cluster_info(self) -> dict[str, Any]:
        """读取 API Server 版本和本次查询使用的 context。"""

        try:
            version = client.VersionApi(self.api_client).get_code()
            return {"context": self.context, "git_version": version.git_version, "platform": version.platform}
        except ApiException as error:
            raise _api_error(error) from error

    def list_namespaces(self, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
        """限量列出 Namespace 及其状态。"""

        limit = _positive_limit(limit, MAX_LIMIT, "limit")
        try:
            response = client.CoreV1Api(self.api_client).list_namespace(limit=limit)
            return [{**_metadata(item), "phase": getattr(item.status, "phase", None)} for item in response.items]
        except ApiException as error:
            raise _api_error(error) from error

    def list_nodes(self, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
        """限量列出节点状态、版本和地址。"""

        limit = _positive_limit(limit, MAX_LIMIT, "limit")
        try:
            response = client.CoreV1Api(self.api_client).list_node(limit=limit)
            result = []
            for item in response.items:
                conditions = {condition.type: condition.status for condition in (item.status.conditions or [])}
                addresses = {address.type: address.address for address in (item.status.addresses or [])}
                result.append({**_metadata(item), "kubelet_version": item.status.node_info.kubelet_version, "conditions": conditions, "addresses": addresses})
            return result
        except ApiException as error:
            raise _api_error(error) from error

    def list_pods(self, namespace: str, label_selector: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
        """按命名空间和可选标签选择器限量列出 Pod 摘要。"""

        limit = _positive_limit(limit, MAX_LIMIT, "limit")
        try:
            response = client.CoreV1Api(self.api_client).list_namespaced_pod(namespace=namespace, label_selector=label_selector, limit=limit)
            return [self._pod_summary(item) for item in response.items]
        except ApiException as error:
            raise _api_error(error) from error

    def get_pod(self, namespace: str, name: str) -> dict[str, Any]:
        """读取单个 Pod 摘要及容器运行状态。"""

        try:
            return self._pod_summary(client.CoreV1Api(self.api_client).read_namespaced_pod(name=name, namespace=namespace))
        except ApiException as error:
            raise _api_error(error) from error

    def list_deployments(self, namespace: str, label_selector: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
        """按命名空间限量列出 Deployment 副本状态。"""

        limit = _positive_limit(limit, MAX_LIMIT, "limit")
        try:
            response = client.AppsV1Api(self.api_client).list_namespaced_deployment(namespace=namespace, label_selector=label_selector, limit=limit)
            return [{**_metadata(item), "desired_replicas": item.spec.replicas, "ready_replicas": item.status.ready_replicas, "available_replicas": item.status.available_replicas} for item in response.items]
        except ApiException as error:
            raise _api_error(error) from error

    def list_events(self, namespace: str, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
        """按命名空间限量列出事件；兼容 event_time 为空的旧事件。"""

        limit = _positive_limit(limit, MAX_LIMIT, "limit")
        try:
            response = client.EventsV1Api(self.api_client).list_namespaced_event(namespace=namespace, limit=limit)
            return [_event_summary(item) for item in response.items]
        except ValueError as error:
            if "event_time" not in str(error) or "None" not in str(error):
                raise
            try:
                response = client.CoreV1Api(self.api_client).list_namespaced_event(namespace=namespace, limit=limit)
                return [_event_summary(item) for item in response.items]
            except ApiException as fallback_error:
                raise _api_error(fallback_error) from fallback_error
        except ApiException as error:
            raise _api_error(error) from error

    def list_config_maps(
        self, namespace: str, label_selector: str | None = None, limit: int = DEFAULT_LIMIT
    ) -> list[dict[str, Any]]:
        """按命名空间限量列出 ConfigMap 摘要，不返回配置内容。"""

        limit = _positive_limit(limit, MAX_LIMIT, "limit")
        try:
            response = client.CoreV1Api(self.api_client).list_namespaced_config_map(
                namespace=namespace, label_selector=label_selector, limit=limit
            )
            return [self._config_map_summary(item) for item in response.items]
        except ApiException as error:
            raise _api_error(error) from error

    def get_config_map(self, namespace: str, name: str) -> dict[str, Any]:
        """读取指定 ConfigMap 的文本配置；二进制字段只返回键名。"""

        try:
            config_map = client.CoreV1Api(self.api_client).read_namespaced_config_map(
                name=name, namespace=namespace
            )
            return {
                **self._config_map_summary(config_map),
                "data": config_map.data or {},
            }
        except ApiException as error:
            raise _api_error(error) from error

    def get_pod_mounts(self, namespace: str, name: str) -> dict[str, Any]:
        """读取 Pod 的卷来源和容器挂载路径，不读取 Secret 或容器文件内容。"""

        try:
            pod = client.CoreV1Api(self.api_client).read_namespaced_pod(
                name=name, namespace=namespace
            )
            return {
                **_metadata(pod),
                "volumes": [self._volume_summary(volume) for volume in (pod.spec.volumes or [])],
                "containers": self._container_mounts(pod.spec.containers or []),
                "init_containers": self._container_mounts(pod.spec.init_containers or []),
            }
        except ApiException as error:
            raise _api_error(error) from error

    def get_pod_logs(self, namespace: str, name: str, container: str, tail_lines: int = DEFAULT_TAIL_LINES, limit_bytes: int = DEFAULT_LOG_BYTES) -> dict[str, Any]:
        """读取指定容器的有限尾部日志，永不跟随持续输出。"""

        tail_lines = _positive_limit(tail_lines, MAX_TAIL_LINES, "tail_lines")
        limit_bytes = _positive_limit(limit_bytes, MAX_LOG_BYTES, "limit_bytes")
        try:
            logs = client.CoreV1Api(self.api_client).read_namespaced_pod_log(name=name, namespace=namespace, container=container, tail_lines=tail_lines, limit_bytes=limit_bytes, follow=False, timestamps=True)
            return {"namespace": namespace, "pod": name, "container": container, "logs": logs}
        except ApiException as error:
            raise _api_error(error) from error

    def download_pod_logs(
        self,
        namespace: str,
        name: str,
        container: str,
        task_id: str,
        tail_lines: int = DEFAULT_TAIL_LINES,
        limit_bytes: int = DEFAULT_LOG_BYTES,
    ) -> dict[str, Any]:
        """下载受限 Pod 日志到当前工作区的临时目录，不接受外部输出路径。"""

        result = self.get_pod_logs(namespace, name, container, tail_lines, limit_bytes)
        encoded_logs = result["logs"].encode("utf-8")
        safe_task = _safe_path_component(task_id, "task_id")
        safe_namespace = _safe_path_component(namespace, "namespace")
        safe_pod = _safe_path_component(name, "name")
        safe_container = _safe_path_component(container, "container")
        output_dir = Path.cwd().resolve() / ".codex-tmp" / "k8s" / safe_task
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output_path = output_dir / f"{safe_namespace}_{safe_pod}_{safe_container}_{timestamp}.log"
        try:
            with output_path.open("x", encoding="utf-8", newline="") as output:
                output.write(result["logs"])
        except OSError as error:
            raise KubernetesQueryError("无法在工作区临时目录写入日志文件") from error
        return {
            "path": str(output_path),
            "bytes_written": len(encoded_logs),
            # API 达到字节上限时无法区分完整日志与截断结果，按可能截断处理。
            "truncated": len(encoded_logs) >= limit_bytes,
            "namespace": namespace,
            "pod": name,
            "container": container,
        }

    @staticmethod
    def _pod_summary(item: Any) -> dict[str, Any]:
        """生成包含容器状态的 Pod 查询摘要。"""

        containers = []
        for state in item.status.container_statuses or []:
            containers.append({"name": state.name, "ready": state.ready, "restart_count": state.restart_count, "image": state.image, "state": "running" if state.state.running else "waiting" if state.state.waiting else "terminated" if state.state.terminated else "unknown"})
        return {**_metadata(item), "phase": item.status.phase, "pod_ip": item.status.pod_ip, "node_name": item.spec.node_name, "containers": containers}

    @staticmethod
    def _config_map_summary(config_map: Any) -> dict[str, Any]:
        """返回 ConfigMap 元数据和键名，避免列表查询传输完整配置。"""

        return {
            **_metadata(config_map),
            "data_keys": sorted((config_map.data or {}).keys()),
            "binary_data_keys": sorted((config_map.binary_data or {}).keys()),
            "immutable": config_map.immutable,
        }

    @staticmethod
    def _volume_summary(volume: Any) -> dict[str, Any]:
        """提取卷来源引用；Secret 仅暴露资源名称，不读取其值。"""

        sources = (
            ("config_map", getattr(volume, "config_map", None), "name"),
            ("persistent_volume_claim", getattr(volume, "persistent_volume_claim", None), "claim_name"),
            ("secret", getattr(volume, "secret", None), "secret_name"),
        )
        for source_type, source, name_field in sources:
            if source is not None:
                return {
                    "name": volume.name,
                    "source_type": source_type,
                    "source_name": getattr(source, name_field, None),
                }
        if getattr(volume, "projected", None) is not None:
            return {"name": volume.name, "source_type": "projected", "source_name": None}
        return {"name": volume.name, "source_type": "other", "source_name": None}

    @staticmethod
    def _container_mounts(containers: list[Any]) -> list[dict[str, Any]]:
        """转换容器挂载关系，供 ConfigMap 与挂载排查关联使用。"""

        return [
            {
                "name": container.name,
                "mounts": [
                    {
                        "volume_name": mount.name,
                        "mount_path": mount.mount_path,
                        "read_only": bool(mount.read_only),
                    }
                    for mount in (container.volume_mounts or [])
                ],
            }
            for container in containers
        ]
