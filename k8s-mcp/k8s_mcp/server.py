"""向 MCP 客户端提供 Kubernetes 只读工具。"""

from mcp.server.fastmcp import FastMCP

from k8s_mcp.client import DEFAULT_LIMIT, DEFAULT_LOG_BYTES, DEFAULT_TAIL_LINES, KubernetesReader
from k8s_mcp.config import KubernetesConfigurationError


def create_server() -> FastMCP:
    """创建 stdio MCP 服务；每个工具均要求调用者提供 kubeconfig。"""

    server = FastMCP("k8s-readonly", instructions="仅查询 Kubernetes 信息。所有工具必须提供绝对 kubeconfig 路径，且不支持任何写入操作。")

    def reader(kubeconfig: str, context: str | None) -> KubernetesReader:
        """统一创建读取器并向 MCP 返回不含敏感信息的配置错误。"""

        try:
            return KubernetesReader.from_kubeconfig(kubeconfig, context)
        except KubernetesConfigurationError as error:
            raise ValueError(str(error)) from error

    @server.tool(name="get_cluster_info", description="读取当前 kubeconfig context 和 Kubernetes API Server 版本。")
    def get_cluster_info(kubeconfig: str, context: str | None = None) -> dict:
        return reader(kubeconfig, context).cluster_info()

    @server.tool(name="list_namespaces", description="限量列出 Kubernetes Namespace。")
    def list_namespaces(kubeconfig: str, context: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
        return reader(kubeconfig, context).list_namespaces(limit)

    @server.tool(name="list_nodes", description="限量列出 Kubernetes 节点状态和版本。")
    def list_nodes(kubeconfig: str, context: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
        return reader(kubeconfig, context).list_nodes(limit)

    @server.tool(name="list_pods", description="按 namespace 限量列出 Pod，支持标签选择器。")
    def list_pods(kubeconfig: str, namespace: str, context: str | None = None, label_selector: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
        return reader(kubeconfig, context).list_pods(namespace, label_selector, limit)

    @server.tool(name="get_pod", description="读取指定 Pod 的状态和容器摘要。")
    def get_pod(kubeconfig: str, namespace: str, name: str, context: str | None = None) -> dict:
        return reader(kubeconfig, context).get_pod(namespace, name)

    @server.tool(name="list_deployments", description="按 namespace 限量列出 Deployment 副本状态。")
    def list_deployments(kubeconfig: str, namespace: str, context: str | None = None, label_selector: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
        return reader(kubeconfig, context).list_deployments(namespace, label_selector, limit)

    @server.tool(name="list_events", description="按 namespace 限量列出 Kubernetes Events v1 事件。")
    def list_events(kubeconfig: str, namespace: str, context: str | None = None, limit: int = DEFAULT_LIMIT) -> list[dict]:
        return reader(kubeconfig, context).list_events(namespace, limit)

    @server.tool(name="get_pod_logs", description="读取指定容器的有限尾部日志，不支持持续跟随。")
    def get_pod_logs(kubeconfig: str, namespace: str, name: str, container: str, context: str | None = None, tail_lines: int = DEFAULT_TAIL_LINES, limit_bytes: int = DEFAULT_LOG_BYTES) -> dict:
        return reader(kubeconfig, context).get_pod_logs(namespace, name, container, tail_lines, limit_bytes)

    @server.tool(name="download_pod_logs", description="下载指定容器的受限尾部日志到当前工作区 .codex-tmp，不支持指定输出路径或持续跟随。")
    def download_pod_logs(kubeconfig: str, namespace: str, name: str, container: str, task_id: str, context: str | None = None, tail_lines: int = DEFAULT_TAIL_LINES, limit_bytes: int = DEFAULT_LOG_BYTES) -> dict:
        return reader(kubeconfig, context).download_pod_logs(namespace, name, container, task_id, tail_lines, limit_bytes)

    return server
