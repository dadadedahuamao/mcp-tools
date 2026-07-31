from pathlib import Path
import sys

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from loki_mcp.config import load_environment


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
