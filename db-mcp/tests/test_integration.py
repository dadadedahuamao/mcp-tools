"""可选的真实数据库连通性测试；默认不启动 Docker。"""

from __future__ import annotations

import os
from pathlib import Path
import sys
from threading import Event, Thread
import time

import pytest
from sqlalchemy import text

if os.getenv("TEST_DATABASE_INTEGRATION") != "1":
    pytest.skip("设置 TEST_DATABASE_INTEGRATION=1 后运行 Docker 集成测试", allow_module_level=True)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from db_mcp.client import DatabaseReader, create_database_engine
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


@pytest.mark.integration
def test_mysql_8_container_exposes_metadata_indexes_and_diagnostics() -> None:
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
            query_limits=QueryLimits(max_rows=100),
        )
        engine = create_database_engine(source)
        try:
            with engine.begin() as connection:
                connection.execute(text("CREATE TABLE parent (id INT PRIMARY KEY) COMMENT = 'parent table'"))
                connection.execute(
                    text(
                        """CREATE TABLE diagnostic_example (
                            id INT PRIMARY KEY,
                            parent_id INT NOT NULL,
                            code VARCHAR(64) NOT NULL,
                            amount INT NOT NULL,
                            CONSTRAINT fk_diagnostic_parent FOREIGN KEY (parent_id) REFERENCES parent(id),
                            CONSTRAINT uq_diagnostic_code UNIQUE (code),
                            CONSTRAINT ck_diagnostic_amount CHECK (amount >= 0),
                            INDEX idx_diagnostic_code_amount (code DESC, amount ASC),
                            INDEX idx_diagnostic_code_prefix (code(8))
                        ) COMMENT = 'diagnostic metadata table'"""
                    )
                )
                connection.execute(text("CREATE INDEX idx_diagnostic_lower_code ON diagnostic_example ((LOWER(code)))"))
                connection.execute(text("INSERT INTO parent(id) VALUES (1)"))
                connection.execute(text("INSERT INTO diagnostic_example(id, parent_id, code, amount) VALUES (1, 1, 'example', 1)"))

            reader = DatabaseReader(source)
            described = reader.describe_table("diagnostic_example")
            indexes = reader.list_indexes(table="diagnostic_example")
            objects = reader.search_objects("diagnostic", object_types=["TABLE", "COLUMN"])
            plan = reader.explain_query("SELECT id FROM diagnostic_example WHERE code = :code", {"code": "example"})
            sessions = reader.get_active_sessions(limit=10)
            locks = reader.get_lock_summary(limit=10)
            slow_queries = reader.search_slow_queries(limit=10)

            assert described["table_comment"] == "diagnostic metadata table"
            assert described["constraints"]["primary_key"]["constrained_columns"] == ["id"]
            assert described["constraints"]["foreign_keys"]
            assert described["constraints"]["unique"]
            assert described["constraints"]["check"]
            assert {row["index_name"] for row in indexes["rows"]} >= {
                "PRIMARY",
                "uq_diagnostic_code",
                "idx_diagnostic_code_amount",
                "idx_diagnostic_code_prefix",
                "idx_diagnostic_lower_code",
            }
            assert any(row["sort_direction"] == "D" for row in indexes["rows"])
            assert any(row["prefix_length"] == 8 for row in indexes["rows"])
            assert any(row["expression"] is not None for row in indexes["rows"])
            assert any(row["object_name"] == "diagnostic_example" for row in objects["rows"])
            assert plan["rows"]
            assert "INFO" not in sessions["columns"]
            assert "DIGEST_TEXT" not in slow_queries["columns"]
            assert {"transaction_id", "thread_id", "lock_mode"} <= set(locks["columns"])
        finally:
            engine.dispose()


@pytest.mark.integration
def test_mysql_8_container_reports_lock_waiting_and_blocking_sessions() -> None:
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
            query_limits=QueryLimits(max_rows=100, timeout_seconds=10),
        )
        blocker_engine = create_database_engine(source)
        waiter_engine = create_database_engine(source)
        started = Event()
        finished = Event()

        def wait_for_lock() -> None:
            try:
                with waiter_engine.connect() as connection:
                    connection.execute(text("SET innodb_lock_wait_timeout = 5"))
                    connection.execute(text("START TRANSACTION"))
                    started.set()
                    connection.execute(text("UPDATE lock_probe SET value = 2 WHERE id = 1"))
                    connection.rollback()
            finally:
                finished.set()

        try:
            with blocker_engine.begin() as setup:
                setup.execute(text("CREATE TABLE lock_probe (id INT PRIMARY KEY, value INT NOT NULL)"))
                setup.execute(text("INSERT INTO lock_probe(id, value) VALUES (1, 1)"))
            with blocker_engine.connect() as blocker:
                blocker.execute(text("START TRANSACTION"))
                blocker.execute(text("UPDATE lock_probe SET value = 1 WHERE id = 1"))
                waiter = Thread(target=wait_for_lock, daemon=True)
                waiter.start()
                assert started.wait(2)
                reader = DatabaseReader(source)
                tree = {"rows": []}
                for _ in range(20):
                    tree = reader.get_lock_tree(limit=10)
                    if tree["rows"]:
                        break
                    time.sleep(0.1)
                assert tree["rows"]
                assert {"waiting_session_id", "blocking_session_id", "waiting_transaction_id", "blocking_transaction_id"} <= set(tree["columns"])
                blocker.rollback()
                assert finished.wait(5)
                waiter.join(timeout=1)
        finally:
            blocker_engine.dispose()
            waiter_engine.dispose()
