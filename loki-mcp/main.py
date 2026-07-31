"""Loki MCP 的命令行入口。"""

import argparse
from pathlib import Path

from loki_mcp.server import create_server


def main() -> None:
    """读取环境配置路径并以 stdio 方式启动 MCP 服务。"""

    parser = argparse.ArgumentParser(description="本地只读 Loki MCP")
    parser.add_argument(
        "--env-file",
        required=True,
        type=Path,
        help="包含环境与 Loki 配置的 env.yaml 绝对路径",
    )
    arguments = parser.parse_args()
    create_server(arguments.env_file).run(transport="stdio")


if __name__ == "__main__":
    main()
