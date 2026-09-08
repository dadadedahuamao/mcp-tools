from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from main import build_parser


def test_build_parser_version() -> None:
    """测试版本参数。"""
    parser = build_parser()
    assert parser.get_default("host") == "127.0.0.1"


def test_build_parser_defaults() -> None:
    """测试默认参数。"""
    parser = build_parser()
    args = parser.parse_args(["--env-file", "/tmp/env.yaml"])
    assert args.transport == "stdio"
    assert args.host == "127.0.0.1"
    assert args.port == 18005
    assert args.allowed_host == []


def test_build_parser_streamable_http() -> None:
    """测试 streamable-http 模式参数。"""
    parser = build_parser()
    args = parser.parse_args([
        "--env-file", "/tmp/env.yaml",
        "--transport", "streamable-http",
        "--host", "0.0.0.0",
        "--port", "18005",
        "--allowed-host", "example.com:18005",
    ])
    assert args.transport == "streamable-http"
    assert args.host == "0.0.0.0"
    assert args.port == 18005
    assert args.allowed_host == ["example.com:18005"]


def test_build_parser_multiple_allowed_hosts() -> None:
    """测试多个 allowed-host 参数。"""
    parser = build_parser()
    args = parser.parse_args([
        "--env-file", "/tmp/env.yaml",
        "--allowed-host", "host1:18005",
        "--allowed-host", "host2:18005",
    ])
    assert args.allowed_host == ["host1:18005", "host2:18005"]
