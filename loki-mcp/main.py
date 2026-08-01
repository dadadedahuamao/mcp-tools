"""Loki MCP 的命令行入口。"""

import argparse
from pathlib import Path

from loki_mcp.server import create_server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="只读 Loki MCP")
    parser.add_argument("--version", action="version", version="loki-mcp 0.1.0")
    parser.add_argument("--env-file", required=True, type=Path, help="env.yaml 绝对路径")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18001)
    parser.add_argument("--allowed-host", action="append", default=[])
    return parser


def main() -> None:
    """读取环境配置路径并启动 stdio 或 Streamable HTTP MCP 服务。"""

    parser = build_parser()
    arguments = parser.parse_args()
    create_server(
        arguments.env_file,
        host=arguments.host,
        port=arguments.port,
        allowed_hosts=arguments.allowed_host if arguments.transport == "streamable-http" else None,
    ).run(transport=arguments.transport)


if __name__ == "__main__":
    main()
