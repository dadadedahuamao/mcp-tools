from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from main import build_parser


def test_parser_defaults_to_stdio() -> None:
    args = build_parser().parse_args(["--config-file", "/etc/db-mcp/datasources.yaml"])

    assert args.transport == "stdio"


def test_parser_accepts_streamable_http_transport() -> None:
    args = build_parser().parse_args(
        ["--config-file", "/etc/db-mcp/datasources.yaml", "--transport", "streamable-http"]
    )

    assert args.transport == "streamable-http"


def test_parser_accepts_explicit_http_allowed_host() -> None:
    args = build_parser().parse_args(
        [
            "--config-file",
            "/etc/db-mcp/datasources.yaml",
            "--transport",
            "streamable-http",
            "--allowed-host",
            "192.168.1.10:8000",
        ]
    )

    assert args.allowed_host == ["192.168.1.10:8000"]


def test_parser_accepts_explicit_http_listener() -> None:
    args = build_parser().parse_args(
        ["--config-file", "/etc/db-mcp/datasources.yaml", "--host", "0.0.0.0", "--port", "8000"]
    )

    assert args.host == "0.0.0.0"
    assert args.port == 8000
