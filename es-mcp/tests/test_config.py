from pathlib import Path
import sys
import tempfile

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from es_mcp.config import ElasticsearchConfigurationError, load_environment


def write_env(tmp_path: Path, es: dict) -> Path:
    env_file = tmp_path / "env.yaml"
    env_file.write_text(
        yaml.safe_dump({"env": [{"env_name": "生产", "es": es}]}, allow_unicode=True),
        encoding="utf-8",
    )
    return env_file


@pytest.fixture
def temp_dir():
    """创建临时目录。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def test_load_environment_basic(temp_dir: Path) -> None:
    """测试基本配置加载。"""
    es = {
        "hosts": ["http://localhost:9200"],
        "username": "admin",
        "password": "secret",
        "index_allowlist": ["*open_api_log*"],
    }
    config = load_environment(write_env(temp_dir, es), "生产")
    assert config.name == "生产"
    assert config.hosts == ("http://localhost:9200",)
    assert config.username == "admin"
    assert config.password == "secret"
    assert "*open_api_log*" in config.index_allowlist


def test_load_environment_multiple_hosts(temp_dir: Path) -> None:
    """测试多节点配置。"""
    es = {
        "hosts": [
            "http://10.180.6.166:9200",
            "http://10.180.4.209:9200",
            "http://10.180.5.157:9200",
        ],
        "username": "admin",
        "password": "xcmg#@!MES01",
        "index_allowlist": ["*open_api_log*", "*third_api_log*"],
    }
    config = load_environment(write_env(temp_dir, es), "生产")
    assert len(config.hosts) == 3


def test_load_environment_treats_missing_or_empty_password_as_no_auth(temp_dir: Path) -> None:
    """测试空密码处理。"""
    for password in [None, ""]:
        es = {"hosts": ["http://localhost:9200"], "index_allowlist": ["*open_api_log*"]}
        if password is not None:
            es["password"] = password
        config = load_environment(write_env(temp_dir, es), "生产")
        assert config.password is None


def test_load_environment_rejects_missing_hosts(temp_dir: Path) -> None:
    """测试缺少 hosts 配置。"""
    es = {"index_allowlist": ["*open_api_log*"]}
    with pytest.raises(ElasticsearchConfigurationError, match="hosts"):
        load_environment(write_env(temp_dir, es), "生产")


def test_load_environment_rejects_empty_hosts(temp_dir: Path) -> None:
    """测试空 hosts 列表。"""
    es = {"hosts": [], "index_allowlist": ["*open_api_log*"]}
    with pytest.raises(ElasticsearchConfigurationError, match="hosts"):
        load_environment(write_env(temp_dir, es), "生产")


def test_load_environment_rejects_missing_index_allowlist(temp_dir: Path) -> None:
    """测试缺少 index_allowlist 配置。"""
    es = {"hosts": ["http://localhost:9200"]}
    with pytest.raises(ElasticsearchConfigurationError, match="index_allowlist"):
        load_environment(write_env(temp_dir, es), "生产")


def test_load_environment_rejects_empty_index_allowlist(temp_dir: Path) -> None:
    """测试空 index_allowlist 列表。"""
    es = {"hosts": ["http://localhost:9200"], "index_allowlist": []}
    with pytest.raises(ElasticsearchConfigurationError, match="index_allowlist"):
        load_environment(write_env(temp_dir, es), "生产")


def test_load_environment_default_values(temp_dir: Path) -> None:
    """测试默认值。"""
    es = {
        "hosts": ["http://localhost:9200"],
        "index_allowlist": ["*open_api_log*"],
    }
    config = load_environment(write_env(temp_dir, es), "生产")
    assert config.timeout_seconds == 30
    assert config.max_result_window == 10000
    assert config.max_response_bytes == 1048576


def test_load_environment_custom_values(temp_dir: Path) -> None:
    """测试自定义值。"""
    es = {
        "hosts": ["http://localhost:9200"],
        "index_allowlist": ["*open_api_log*"],
        "timeout_seconds": 60,
        "max_result_window": 5000,
        "max_response_bytes": 2097152,
    }
    config = load_environment(write_env(temp_dir, es), "生产")
    assert config.timeout_seconds == 60
    assert config.max_result_window == 5000
    assert config.max_response_bytes == 2097152


def test_load_environment_alias_matching(temp_dir: Path) -> None:
    """测试环境别名匹配。"""
    es = {
        "hosts": ["http://localhost:9200"],
        "index_allowlist": ["*open_api_log*"],
    }
    env_file = temp_dir / "env.yaml"
    env_file.write_text(
        yaml.safe_dump({"env": [{"env_name": "UAT环境", "es": es}]}, allow_unicode=True),
        encoding="utf-8",
    )
    # 精确匹配
    config = load_environment(env_file, "UAT环境")
    assert config.name == "UAT环境"
    # 归一化匹配
    config = load_environment(env_file, "uat环境")
    assert config.name == "UAT环境"


def test_load_environment_rejects_non_dict_es_config(temp_dir: Path) -> None:
    """测试非字典 ES 配置。"""
    env_file = temp_dir / "env.yaml"
    env_file.write_text(
        yaml.safe_dump({"env": [{"env_name": "生产", "es": "invalid"}]}, allow_unicode=True),
        encoding="utf-8",
    )
    with pytest.raises(ElasticsearchConfigurationError, match="未配置 Elasticsearch"):
        load_environment(env_file, "生产")
