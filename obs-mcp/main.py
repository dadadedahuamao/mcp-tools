import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="只读 S3 兼容 OBS MCP")
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18004)
    parser.add_argument("--allowed-host", action="append", default=[])
    args = parser.parse_args()
    from obs_mcp.server import create_server
    create_server(args.env_file, host=args.host, port=args.port, allowed_hosts=args.allowed_host if args.transport == "streamable-http" else None).run(transport=args.transport)


if __name__ == "__main__":
    main()
