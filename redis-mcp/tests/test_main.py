from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from main import build_parser


def test_parser_uses_redis_default_port() -> None:
    assert build_parser().parse_args(["--env-file", "env.yaml"]).port == 18003
