from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loki_mcp.server import parse_timestamp


def test_parse_timestamp_requires_timezone() -> None:
    with pytest.raises(ValueError, match="时区"):
        parse_timestamp("2026-07-29T12:00:00")


def test_parse_timestamp_accepts_offset() -> None:
    timestamp = parse_timestamp("2026-07-29T12:00:00+08:00")

    assert timestamp.isoformat() == "2026-07-29T12:00:00+08:00"
