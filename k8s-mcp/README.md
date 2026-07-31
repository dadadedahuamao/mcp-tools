# K8s Read-only MCP

本地 stdio MCP，使用调用参数中的 kubeconfig 查询 Kubernetes 集群。服务不依赖 `kubectl`，且没有创建、修改、删除、命令执行、端口转发或日志跟随工具。

## 开发与构建

```powershell
cd E:\Project\cxh\mcp-tools\k8s-mcp
D:\Program Files\Python\Python312\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test,build]"
.\build.ps1
```

构建成功后，交付 `dist\k8s-mcp.exe`。该 Windows x64 单文件程序不要求使用者安装 Python、Python 依赖或 `kubectl`。

## Codex 配置

在 `C:\Users\<用户名>\.codex\config.toml` 添加：

```toml
[mcp_servers.k8s_readonly]
command = "E:\\Project\\xcmg\\xcmg_uat\\mom-backend\\tools\\k8s-mcp\\k8s-mcp.exe"
```

重启 Codex 并新开任务。每个工具调用都必须传入目标集群的 kubeconfig **绝对路径**，例如：

```text
kubeconfig: "E:\\下载\\谷歌浏览器下载\\cce-cloudpond-uat-mes-kubeconfig.yaml"
namespace: "ns-mfg-mes-jc"
limit: 100
```

可选 `context` 可指定 kubeconfig 中的 context；未提供时使用 kubeconfig 的 `current-context`。

## 可用工具与限制

| 工具 | 作用 | 限制 |
| --- | --- | --- |
| `get_cluster_info` | 查询 context 和 API Server 版本 | 只读 |
| `list_namespaces` / `list_nodes` | 列出基础集群信息 | `limit` 为 1-500 |
| `list_pods` / `get_pod` | 查询 Pod 与容器状态 | `list_pods` 支持标签选择器，`limit` 为 1-500 |
| `list_deployments` | 查询 Deployment 副本状态 | 支持标签选择器，`limit` 为 1-500 |
| `list_events` | 查询 Events v1 事件 | `limit` 为 1-500 |
| `list_config_maps` / `get_config_map` | 查询 ConfigMap 键名或文本 data | 不读取 Secret；列表查询支持标签选择器，`limit` 为 1-500 |
| `get_pod_mounts` | 查询 Pod 卷来源和容器挂载路径 | Secret 仅返回名称引用，不读取内容；不执行容器命令 |
| `get_pod_logs` | 获取指定容器的尾部日志 | 不支持 follow；`tail_lines` 为 1-2000，`limit_bytes` 为 1-262144 |
| `download_pod_logs` | 下载指定容器的尾部日志 | 写入当前工作区 `.codex-tmp/k8s/<task_id>/`；不允许指定输出路径；与 `get_pod_logs` 使用相同限制 |

## 安全边界

- kubeconfig 只在当前工具调用内加载，服务不会读取默认 kubeconfig。
- 响应不返回 kubeconfig、token、客户端证书、私钥或认证请求头。
- API 错误被转换为中文概述，不返回原始 HTTP 响应内容。
- 是否能查询某资源仍由 kubeconfig 对应身份的 Kubernetes RBAC 权限决定。
