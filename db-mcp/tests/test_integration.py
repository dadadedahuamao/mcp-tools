"""可选的真实数据库连通性测试；默认不启动 Docker。"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

if os.getenv("TEST_DATABASE_INTEGRATION") != "1":
    pytest.skip("设置 TEST_DATABASE_INTEGRATION=1 后运行 Docker 集成测试", allow_module_level=True)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from db_mcp.client import DatabaseReader
from db_mcp.config import DatabaseSource, QueryLimits


@pytest.mark.integration
def test_postgresql_container_executes_parameterized_readonly_query() -> None:
    containers = pytest.importorskip("testcontainers.postgres")
    with containers.PostgresContainer("postgres:16-alpine") as container:
        source = DatabaseSource(
            alias="postgres",
            dialect="postgresql",
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(5432)),
            database=container.dbname,
            username=container.username,
            password_env="TEST_ONLY",
            password=container.password,
            default_schema="public",
            allowed_schemas=frozenset({"public"}),
            tls=None,
            query_limits=QueryLimits(max_rows=10),
        )
        result = DatabaseReader(source).query("SELECT :value AS value", {"value": 7})
    assert result["rows"] == [{"value": 7}]


@pytest.mark.integration
def test_mysql_container_executes_parameterized_readonly_query() -> None:
    containers = pytest.importorskip("testcontainers.mysql")
    with containers.MySqlContainer("mysql:8.4") as container:
        source = DatabaseSource(
            alias="mysql",
            dialect="mysql",
            host=container.get_container_host_ip(),
            port=int(container.get_exposed_port(3306)),
            database=container.dbname,
            username=container.username,
            password_env="TEST_ONLY",
            password=container.password,
            default_schema=container.dbname,
            allowed_schemas=frozenset({container.dbname}),
            tls=None,
            query_limits=QueryLimits(max_rows=10),
        )
        result = DatabaseReader(source).query("SELECT :value AS value", {"value": 7})
    assert result["rows"] == [{"value": 7}]
