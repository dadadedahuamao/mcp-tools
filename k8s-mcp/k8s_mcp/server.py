"""向 MCP 客户端提供 Kubernetes 只读工具。"""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from k8s_mcp.client import DEFAULT_LIMIT, DEFAULT_LOG_BYTES, DEFAULT_TAIL_LINES, KubernetesReader
from k8s_mcp.config import KubernetesConfigurationError, RegisteredCluster


def create_server(
    clusters: dict[str, RegisteredCluster] | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    allowed_hosts: list[str] | None = None,
) -> FastMCP:
    """创建 MCP；远程模式使用服务端集群别名，本地模式兼容 kubeconfig 参数。"""

    instructions = "仅查询 Kubernetes 信息。"
    if clusters is not None:
        instructions += "远程服务仅接受预注册 cluster 别名，不接受调用方传入 kubeconfig。"
    else:
        instructions += "本地调用必须提供绝对 kubeconfig 路径。"
    server = FastMCP("k8s-readonly", instructions=instructions, host=host, port=port, transport_security=(TransportSecuritySettings(allowed_hosts=allowed_hosts) if allowed_hosts else None))

    def reader(kubeconfig: str | None, cluster: str | None, context: str | None) -> KubernetesReader:
        try:
            if clusters is not None:
                if kubeconfig is not None:
                    raise KubernetesConfigurationError("远程服务不接受 kubeconfig，请传入 cluster 别名")
                if not cluster or cluster not in clusters:
                    raise KubernetesConfigurationError("未找到预注册集群别名")
                selected = clusters[cluster]
                return KubernetesReader.from_kubeconfig(str(selected.kubeconfig), context or selected.context)
            if not kubeconfig:
                raise KubernetesConfigurationError("本地调用必须提供 kubeconfig")
            return KubernetesReader.from_kubeconfig(kubeconfig, context)
        except KubernetesConfigurationError as error:
            raise ValueError(str(error)) from None

    @server.tool(name="get_cluster_info", description="读取目标集群 context 和 API Server 版本。")
    def get_cluster_info(kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None) -> dict:
        return reader(kubeconfig, cluster, context).cluster_info()

    @server.tool(name="list_namespaces", description="限量列出 Kubernetes Namespace。")
    def list_namespaces(kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
        return reader(kubeconfig, cluster, context).list_namespaces(limit)

    @server.tool(name="list_nodes", description="限量列出 Kubernetes 节点状态和版本。")
    def list_nodes(kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
        return reader(kubeconfig, cluster, context).list_nodes(limit)

    @server.tool(name="list_pods", description="按 namespace 限量列出 Pod，支持标签选择器。")
    def list_pods(namespace: str, kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None, label_selector: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
        return reader(kubeconfig, cluster, context).list_pods(namespace, label_selector, limit)

    @server.tool(name="get_pod", description="读取指定 Pod 的状态和容器摘要。")
    def get_pod(namespace: str, name: str, kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None) -> dict:
        return reader(kubeconfig, cluster, context).get_pod(namespace, name)

    @server.tool(name="list_deployments", description="按 namespace 限量列出 Deployment 副本状态。")
    def list_deployments(namespace: str, kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None, label_selector: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
        return reader(kubeconfig, cluster, context).list_deployments(namespace, label_selector, limit)

    @server.tool(name="list_events", description="按 namespace 限量列出 Kubernetes Events v1 事件。")
    def list_events(namespace: str, kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
        return reader(kubeconfig, cluster, context).list_events(namespace, limit)

    @server.tool(name="list_config_maps", description="按 namespace 限量列出 ConfigMap 摘要和键名，支持标签选择器。")
    def list_config_maps(namespace: str, kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None, label_selector: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
        return reader(kubeconfig, cluster, context).list_config_maps(namespace, label_selector, limit)

    @server.tool(name="get_config_map", description="读取指定 ConfigMap 的文本 data；不会读取 Secret 或执行容器命令。")
    def get_config_map(namespace: str, name: str, kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None) -> dict:
        return reader(kubeconfig, cluster, context).get_config_map(namespace, name)

    @server.tool(name="get_pod_mounts", description="读取 Pod 的卷来源和容器挂载路径；Secret 仅返回名称引用。")
    def get_pod_mounts(namespace: str, name: str, kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None) -> dict:
        return reader(kubeconfig, cluster, context).get_pod_mounts(namespace, name)

    @server.tool(name="get_pod_logs", description="读取指定容器的有限尾部日志，不支持持续跟随。")
    def get_pod_logs(namespace: str, name: str, container: str, kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None, tail_lines: int = DEFAULT_TAIL_LINES, limit_bytes: int = DEFAULT_LOG_BYTES) -> dict:
        return reader(kubeconfig, cluster, context).get_pod_logs(namespace, name, container, tail_lines, limit_bytes)

    @server.tool(name="download_pod_logs", description="下载指定容器的受限尾部日志到当前工作区 .codex-tmp，不支持指定输出路径或持续跟随。")
    def download_pod_logs(namespace: str, name: str, container: str, task_id: str, kubeconfig: str | None = None, cluster: str | None = None, context: str | None = None, tail_lines: int = DEFAULT_TAIL_LINES, limit_bytes: int = DEFAULT_LOG_BYTES) -> dict:
        return reader(kubeconfig, cluster, context).download_pod_logs(namespace, name, container, task_id, tail_lines, limit_bytes)

    return server
