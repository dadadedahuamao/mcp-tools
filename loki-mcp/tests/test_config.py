from pathlib import Path
import sys

import yaml
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loki_mcp.config import LokiConfigurationError, load_environment


def test_load_environment_returns_exact_loki_configuration(tmp_path: Path) -> None:
    env_file = tmp_path / "env.yaml"
    env_file.write_text(
        yaml.safe_dump(
            {
                "env": [
                    {
                        "env_name": "矿机(一期生产)",
                        "loki_url": "https://mes.xcmg.com/grafana/",
                        "loki_datasource_uid": "loki-production",
                        "loki_user_name": "readonly",
                        "loki_passwd": "secret",
                        "loki_query": '{app="mmom-kj"}',
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    environment = load_environment(env_file, "矿机(一期生产)")

    assert environment.name == "矿机(一期生产)"
    assert environment.base_url == "https://mes.xcmg.com/grafana"
    assert environment.datasource_uid == "loki-production"
    assert environment.query == '{app="mmom-kj"}'


def test_load_environment_accepts_unique_business_name(tmp_path: Path) -> None:
    env_file = tmp_path / "env.yaml"
    env_file.write_text(
        yaml.safe_dump(
            {"env": [{"env_name": "道路(道路、筑路、养护)(二期UAT)", "loki_url": "https://grafana.example", "loki_datasource_uid": "road", "loki_user_name": "readonly", "loki_passwd": "secret", "loki_query": '{app="road"}'}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    environment = load_environment(env_file, "二期道路环境")

    assert environment.name == "道路(道路、筑路、养护)(二期UAT)"


def test_load_environment_rejects_ambiguous_business_name(tmp_path: Path) -> None:
    env_file = tmp_path / "env.yaml"
    env_file.write_text(
        yaml.safe_dump(
            {"env": [{"env_name": "重型(一期UAT)"}, {"env_name": "重型车辆(二期UAT)"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(LokiConfigurationError, match="不唯一"):
        load_environment(env_file, "重型")
