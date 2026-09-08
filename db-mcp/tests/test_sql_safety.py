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


def validate_oracle(sql: str, parameters: dict[str, object] | None = None):
    return validate_readonly_query(
        sql,
        dialect="oracle",
        default_schema="MFG_MES_SITE_DL",
        allowed_schemas=frozenset({"MFG_MES_SITE_DL"}),
        parameters=parameters,
    )


def validate_dialect(sql: str, dialect: str, parameters: dict[str, object] | None = None):
    return validate_readonly_query(
        sql,
        dialect=dialect,
        default_schema="public",
        allowed_schemas=frozenset({"public"}),
        parameters=parameters,
    )


@pytest.mark.parametrize(
    ("dialect", "expected_identifier"),
    [
        ("postgresql", '"order"'),
        ("mysql", "`order`"),
    ],
)
def test_quotes_reserved_column_for_target_dialect(dialect: str, expected_identifier: str) -> None:
    result = validate_dialect("SELECT order FROM task", dialect)

    assert expected_identifier in result.sql
    assert f"{dialect}_reserved_identifier" in result.adaptations


@pytest.mark.parametrize(
    ("dialect", "expected_sql"),
    [
        ("postgresql", 'SELECT "order".id FROM public.task AS "order"'),
        ("mysql", "SELECT `order`.id FROM public.task AS `order`"),
    ],
)
def test_quotes_reserved_table_alias_and_its_references(dialect: str, expected_sql: str) -> None:
    result = validate_dialect("SELECT order.id FROM task AS order", dialect)

    assert result.sql == expected_sql


def test_oracle_quotes_reserved_column_identifiers() -> None:
    result = validate_oracle("SELECT po.NUMBER FROM mbm_aps_product_order po")

    assert 'po."NUMBER"' in result.sql
    assert "oracle_reserved_identifier" in result.adaptations


def test_oracle_adds_dual_for_select_without_from_clause() -> None:
    result = validate_oracle("SELECT 1 AS probe")

    assert result.sql == "SELECT 1 AS probe FROM DUAL"
    assert "oracle_dual" in result.adaptations


def test_oracle_preserves_explicitly_quoted_reserved_column() -> None:
    result = validate_oracle('SELECT po."NUMBER" FROM mbm_aps_product_order po')

    assert result.sql.count('"NUMBER"') == 1
    assert "oracle_reserved_identifier" not in result.adaptations


def test_oracle_renames_reserved_bind_and_migrates_parameter() -> None:
    result = validate_oracle(
        "SELECT po.NUMBER FROM mbm_aps_product_order po WHERE po.NUMBER = :number",
        {"number": "10000889224"},
    )

    assert ":p_number" in result.sql
    assert ":number" not in result.sql
    assert result.parameters == {"p_number": "10000889224"}
    assert "oracle_bind_parameter" in result.adaptations


def test_oracle_reserved_bind_requires_matching_parameter() -> None:
    with pytest.raises(QueryValidationError, match="参数"):
        validate_oracle("SELECT 1 FROM DUAL WHERE 1 = :number", {})


def test_oracle_bind_migration_avoids_existing_name_collision() -> None:
    result = validate_oracle(
        "SELECT 1 FROM DUAL WHERE 1 = :number AND 2 = :p_number",
        {"number": 1, "p_number": 2},
    )

    assert ":p_number_2" in result.sql
    assert result.parameters == {"p_number_2": 1, "p_number": 2}


@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        ("oracle", "SELECT * FROM information_schema.columns"),
        ("mysql", "SELECT * FROM pg_catalog.pg_tables"),
        ("postgresql", "SELECT DATABASE()"),
    ],
)
def test_rejects_high_confidence_dialect_mismatch(dialect: str, sql: str) -> None:
    with pytest.raises(QueryValidationError) as error:
        validate_readonly_query(
            sql,
            dialect=dialect,
            default_schema="public",
            allowed_schemas=frozenset({"public"}),
        )

    assert error.value.code == "dialect_mismatch"
    assert "方言" in str(error.value)


def test_validate_readonly_query_accepts_select_and_applies_default_schema() -> None:
    result = validate("SELECT id, name FROM orders WHERE id = :order_id")

    assert "public.orders" in result.sql
    assert result.referenced_schemas == frozenset({"public"})


def test_validate_readonly_query_accepts_cte_and_multiple_whitelisted_schemas() -> None:
    result = validate(
        "WITH recent AS (SELECT id FROM reporting.orders) SELECT id FROM recent"
    )

    assert result.referenced_schemas == frozenset({"reporting"})


def test_oracle_dual_keeps_public_synonym_unqualified() -> None:
    result = validate_readonly_query(
        "SELECT 1 AS connected FROM DUAL",
        dialect="oracle",
        default_schema="MFG_MES_SITE_DL",
        allowed_schemas=frozenset({"MFG_MES_SITE_DL"}),
    )

    assert result.sql == "SELECT 1 AS connected FROM DUAL"


def test_oracle_explicit_sys_dual_remains_outside_schema_allowlist() -> None:
    with pytest.raises(QueryValidationError, match="schema"):
        validate_readonly_query(
            "SELECT 1 FROM SYS.DUAL",
            dialect="oracle",
            default_schema="MFG_MES_SITE_DL",
            allowed_schemas=frozenset({"MFG_MES_SITE_DL"}),
        )


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
