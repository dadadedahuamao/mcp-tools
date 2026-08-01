from pathlib import Path
import sys

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from redis_mcp.config import RedisConfigurationError, load_environment


def write_env(tmp_path: Path, redis: dict) -> Path:
    env_file = tmp_path / "env.yaml"
    env_file.write_text(
        yaml.safe_dump({"env": [{"env_name": "生产", "redis": redis}]}, allow_unicode=True),
        encoding="utf-8",
    )
    return env_file


@pytest.mark.parametrize("password", [None, ""])
def test_load_environment_treats_missing_or_empty_password_as_no_auth(
    tmp_path: Path, password: str | None
) -> None:
    redis = {"mode": "standalone", "host": "redis", "port": 6379}
    if password is not None:
        redis["password"] = password

    config = load_environment(write_env(tmp_path, redis), "生产")

    assert config.password is None
    assert config.host == "redis"


@pytest.mark.parametrize(
    ("mode", "extra"),
    [
        ("sentinel", {"master_name": "mymaster", "sentinels": [{"host": "s1", "port": 26379}]}),
        ("cluster", {"startup_nodes": [{"host": "r1", "port": 6379}]}),
    ],
)
def test_load_environment_supports_redis_topologies(
    tmp_path: Path, mode: str, extra: dict
) -> None:
    config = load_environment(write_env(tmp_path, {"mode": mode, **extra}), "生产")

    assert config.mode == mode


def test_load_environment_rejects_missing_mode_specific_fields(tmp_path: Path) -> None:
    with pytest.raises(RedisConfigurationError, match="startup_nodes"):
        load_environment(write_env(tmp_path, {"mode": "cluster"}), "生产")


def test_load_environment_rejects_nonzero_database_for_cluster(tmp_path: Path) -> None:
    with pytest.raises(RedisConfigurationError, match="DB 0"):
        load_environment(
            write_env(
                tmp_path,
                {"mode": "cluster", "database": 1, "startup_nodes": [{"host": "r1", "port": 6379}]},
            ),
            "生产",
        )
