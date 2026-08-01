"""Kubernetes 只读 MCP 的命令行入口。"""

import argparse
from pathlib import Path

from k8s_mcp.config import KubernetesConfigurationError, load_cluster_registry, load_environment_registry
from k8s_mcp.server import create_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kubernetes 只读 MCP")
    parser.add_argument("--version", action="version", version="k8s-mcp 0.1.0")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--clusters-file", type=Path, help="远程服务的 clusters.yaml 绝对路径")
    parser.add_argument("--env-file", type=Path, help="远程服务的统一 env.yaml 绝对路径")
    parser.add_argument("--kubeconfig-dir", type=Path, help="统一 env.yaml 引用的 kubeconfig 所在绝对目录")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18002)
    parser.add_argument("--allowed-host", action="append", default=[])
    return parser


def main() -> None:
    """通过 stdio 启动 MCP 服务，不读取默认 kubeconfig。"""

    parser = build_parser()
    args = parser.parse_args()
    if args.clusters_file and args.env_file:
        parser.error("--clusters-file 与 --env-file 只能二选一")
    if args.env_file and args.kubeconfig_dir is None:
        parser.error("使用 --env-file 时必须提供 --kubeconfig-dir")
    if args.transport == "streamable-http" and args.clusters_file is None and args.env_file is None:
        parser.error("远程 streamable-http 模式必须提供 --clusters-file 或 --env-file")
    try:
        clusters = (
            load_cluster_registry(args.clusters_file)
            if args.clusters_file
            else load_environment_registry(args.env_file, args.kubeconfig_dir)
            if args.env_file
            else None
        )
    except KubernetesConfigurationError as error:
        parser.error(str(error))
    create_server(clusters, host=args.host, port=args.port, allowed_hosts=args.allowed_host if args.transport == "streamable-http" else None).run(transport=args.transport)


if __name__ == "__main__":
    main()
