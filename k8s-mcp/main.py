"""Kubernetes 只读 MCP 的命令行入口。"""

import argparse

from k8s_mcp.server import create_server


def main() -> None:
    """通过 stdio 启动 MCP 服务，不读取默认 kubeconfig。"""

    parser = argparse.ArgumentParser(description="本地 Kubernetes 只读 MCP（stdio）")
    parser.add_argument("--version", action="version", version="k8s-mcp 0.1.0")
    parser.parse_args()
    create_server().run(transport="stdio")


if __name__ == "__main__":
    main()
