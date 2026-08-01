from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from db_mcp.config import DatabaseConfigurationError, load_config, load_source


def write_config(tmp_path: Path, content: dict[str, object]) -> Path:
    config_file = tmp_path / "datasources.yaml"
    config_file.write_text(yaml.safe_dump(content, allow_unicode=True), encoding="utf-8")
    return config_file


def valid_source(**overrides: object) -> dict[str, object]:
    source: dict[str, object] = {
        "dialect": "postgresql",
        "host": "db.internal",
        "port": 5432,
        "database": "operations",
        "username": "readonly",
        "password_env": "DATABASE_MCP_TEST_PASSWORD",
        "default_schema": "public",
        "allowed_schemas": ["public", "reporting"],
        "query_limits": {"max_rows": 100, "timeout_seconds": 15},
    }
    source.update(overrides)
    return source


def test_load_config_returns_safe_database_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_MCP_TEST_PASSWORD", "do-not-return-me")
    config_path = write_config(tmp_path, {"datasources": {"production": valid_source()}})

    config = load_config(config_path.resolve())
    source = load_source(config, "production")

    assert source.alias == "production"
    assert source.dialect == "postgresql"
    assert source.password == "do-not-return-me"
    assert source.default_schema == "public"
    assert source.allowed_schemas == frozenset({"public", "reporting"})
    assert "do-not-return-me" not in repr(source)
    assert source.safe_summary() == {
        "alias": "production",
        "dialect": "postgresql",
        "default_schema": "public",
        "allowed_schemas": ["public", "reporting"],
    }


def test_load_config_accepts_unified_env_yaml(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path,
        {
            "env": [
                {
                    "env_name": "UAT",
                    "db": {"connect_name": "mes_pg", "type": "postgres", "host": "db.internal", "port": "5432", "name": "operations", "user": "readonly", "password": "local-only-password", "schema": "public"},
                }
            ]
        },
    )

    source = load_source(load_config(config_path.resolve()), "mes_pg")

    assert source.dialect == "postgresql"
    assert source.database == "operations"
    assert source.default_schema == "public"
    assert source.allowed_schemas == frozenset({"public"})


def test_load_config_rejects_legacy_flat_env_fields(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, {"env": [{"env_name": "UAT", "db_connect_name": "legacy"}]})
    with pytest.raises(DatabaseConfigurationError, match="db 配置块"):
        load_config(config_path.resolve())


def test_load_config_requires_an_absolute_path(tmp_path: Path) -> None:
    config_path = write_config(tmp_path, {"datasources": {"production": valid_source()}})

    with pytest.raises(DatabaseConfigurationError, match="绝对路径"):
        load_config(Path(config_path.name))


def test_load_config_rejects_missing_password_environment_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_MCP_TEST_PASSWORD", raising=False)
    config_path = write_config(tmp_path, {"datasources": {"production": valid_source()}})

    with pytest.raises(DatabaseConfigurationError, match="密码环境变量未设置") as error:
        load_config(config_path.resolve())

    assert "DATABASE_MCP_TEST_PASSWORD" in str(error.value)


def test_load_config_accepts_plaintext_password_without_exposing_it(tmp_path: Path) -> None:
    source = valid_source(password="local-only-password")
    source.pop("password_env")
    config_path = write_config(tmp_path, {"datasources": {"local": source}})

    loaded = load_source(load_config(config_path.resolve()), "local")

    assert loaded.password == "local-only-password"
    assert "local-only-password" not in repr(loaded)


@pytest.mark.parametrize(
    ("dialect", "extra", "expected_database"),
    [
        ("mysql", {"database": "inventory", "port": 3306}, "inventory"),
        ("postgresql", {"database": "inventory", "port": 5432}, "inventory"),
        ("oracle", {"service_name": "ORCLPDB1", "port": 1521}, "ORCLPDB1"),
    ],
)
def test_load_config_accepts_supported_dialects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dialect: str,
    extra: dict[str, object],
    expected_database: str,
) -> None:
    monkeypatch.setenv("DATABASE_MCP_TEST_PASSWORD", "secret")
    source = valid_source(dialect=dialect, **extra)
    if dialect == "oracle":
        source.pop("database")
    config_path = write_config(tmp_path, {"datasources": {"primary": source}})

    loaded = load_source(load_config(config_path.resolve()), "primary")

    assert loaded.database == expected_database


def test_load_config_rejects_nonwhitelisted_default_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_MCP_TEST_PASSWORD", "secret")
    config_path = write_config(
        tmp_path,
        {"datasources": {"production": valid_source(default_schema="private")}},
    )

    with pytest.raises(DatabaseConfigurationError, match="default_schema"):
        load_config(config_path.resolve())


def test_load_source_accepts_unique_business_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_MCP_TEST_PASSWORD", "secret")
    config_path = write_config(tmp_path, {"datasources": {"道路(二期UAT)": valid_source()}})

    source = load_source(load_config(config_path.resolve()), "二期道路环境")

    assert source.alias == "道路(二期UAT)"


def test_load_source_rejects_ambiguous_business_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_MCP_TEST_PASSWORD", "secret")
    config_path = write_config(tmp_path, {"datasources": {"重型(一期UAT)": valid_source(), "重型车辆(二期UAT)": valid_source(database="vehicle")}})

    with pytest.raises(DatabaseConfigurationError, match="不唯一"):
        load_source(load_config(config_path.resolve()), "重型")
