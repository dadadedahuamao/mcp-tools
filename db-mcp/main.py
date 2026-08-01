"""数据库只读 MCP 的命令行入口。"""

from __future__ import annotations

import argparse
from pathlib import Path

from db_mcp.config import DatabaseConfigurationError, load_config
from db_mcp.server import create_server


def build_parser() -> argparse.ArgumentParser:
    """创建同时支持本地 stdio 与远程 Streamable HTTP 的命令行参数。"""

    parser = argparse.ArgumentParser(description="数据库只读 MCP")
    parser.add_argument("--version", action="version", version="db-mcp 0.1.0")
    parser.add_argument("--config-file", required=True, help="数据源 YAML 的绝对路径")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="MCP 传输方式；服务端部署使用 streamable-http",
    )
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="HTTP 请求允许的 Host（可重复传入，如 192.168.1.10:8000）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 监听地址")
    parser.add_argument("--port", type=int, default=8000, help="HTTP 监听端口")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        config = load_config(Path(args.config_file))
    except DatabaseConfigurationError as error:
        parser.error(str(error))
    server = create_server(
        config,
        host=args.host,
        port=args.port,
        allowed_hosts=args.allowed_host if args.transport == "streamable-http" else None,
    )
    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
