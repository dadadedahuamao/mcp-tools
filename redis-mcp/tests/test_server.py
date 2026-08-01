from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from redis_mcp.server import create_server


def test_server_accepts_http_listener_settings(tmp_path: Path) -> None:
    server = create_server(tmp_path / "env.yaml", host="0.0.0.0", allowed_hosts=["redis.example:18003"])
    assert server.settings.host == "0.0.0.0"
    assert server.settings.port == 18003
