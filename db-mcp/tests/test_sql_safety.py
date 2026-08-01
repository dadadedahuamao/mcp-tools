from __future__ import annotations

from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from db_mcp.sql_safety import QueryValidationError, validate_readonly_query


def validate(sql: str):
    return validate_readonly_query(
        sql,
        dialect="postgresql",
        default_schema="public",
        allowed_schemas=frozenset({"public", "reporting"}),
    )


def test_validate_readonly_query_accepts_select_and_applies_default_schema() -> None:
    result = validate("SELECT id, name FROM orders WHERE id = :order_id")

    assert "public.orders" in result.sql
    assert result.referenced_schemas == frozenset({"public"})


def test_validate_readonly_query_accepts_cte_and_multiple_whitelisted_schemas() -> None:
    result = validate(
        "WITH recent AS (SELECT id FROM reporting.orders) SELECT id FROM recent"
    )

    assert result.referenced_schemas == frozenset({"reporting"})


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM public.orders; DELETE FROM public.orders",
        "DELETE FROM public.orders",
        "UPDATE public.orders SET name = 'unsafe'",
        "CREATE TABLE public.stolen (id INTEGER)",
        "CALL public.rebuild_cache()",
        "SELECT * FROM public.orders FOR UPDATE",
        "SELECT * INTO OUTFILE '/tmp/orders.csv' FROM public.orders",
    ],
)
def test_validate_readonly_query_rejects_mutation_and_escape_hatches(sql: str) -> None:
    with pytest.raises(QueryValidationError):
        validate(sql)


def test_validate_readonly_query_rejects_nonwhitelisted_schema() -> None:
    with pytest.raises(QueryValidationError, match="schema 白名单"):
        validate("SELECT * FROM secrets.credentials")


def test_validate_readonly_query_rejects_invalid_syntax_without_echoing_sql() -> None:
    sql = "SELECT password FROM public.users WHERE password = 'super-secret'"
    with pytest.raises(QueryValidationError) as error:
        validate(sql + " AND (")

    assert "super-secret" not in str(error.value)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_terminate_backend(123)",
        "SELECT set_config('search_path', 'secret', false)",
        "SELECT nextval('public.sequence')",
        "SELECT get_lock('exclusive', 1)",
    ],
)
def test_validate_readonly_query_rejects_side_effect_functions(sql: str) -> None:
    with pytest.raises(QueryValidationError, match="函数"):
        validate(sql)


def test_validate_readonly_query_keeps_sqlalchemy_named_parameters() -> None:
    result = validate_readonly_query(
        "SELECT id FROM orders WHERE id = :order_id",
        dialect="postgresql",
        default_schema="public",
        allowed_schemas=frozenset({"public"}),
    )

    assert ":order_id" in result.sql


@pytest.mark.parametrize("sql", ["SELECT * FROM orders LOCK IN SHARE MODE", "SELECT pg_advisory_lock(1)"])
def test_validate_readonly_query_rejects_other_locks(sql: str) -> None:
    with pytest.raises(QueryValidationError):
        validate(sql)


@pytest.mark.parametrize("sql", ["SELECT mutate_payroll()", "SELECT evil_pkg.do_write() FROM dual", "SELECT * FROM public.orders@evil_link"])
def test_validate_readonly_query_rejects_unknown_functions_and_db_links(sql: str) -> None:
    with pytest.raises(QueryValidationError):
        validate_readonly_query(sql, dialect="oracle", default_schema="public", allowed_schemas=frozenset({"public"}))
