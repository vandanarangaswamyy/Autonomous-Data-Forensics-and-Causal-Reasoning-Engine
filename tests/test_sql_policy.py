import pytest
from atlas_contracts.analytics import AnalyticsError, QueryRequest
from atlas_database.analytics.policy import SQLPolicy


@pytest.mark.parametrize("dialect,schema", [("postgres", "public"), ("teradata", "retail")])
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM orders",
        "SELECT 1; DELETE FROM orders",
        "WITH x AS (DELETE FROM orders RETURNING *) SELECT * FROM x",
        "SELECT * INTO stolen FROM orders",
        "SELECT * FROM orders FOR UPDATE",
        "COPY orders TO '/tmp/stolen'",
        "SELECT pg_sleep(10)",
        "SELECT pg_read_file('/etc/passwd')",
        "SELECT set_config('x', 'y', false)",
        "SELECT * FROM event_outbox",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM private.orders",
        "SELECT * FROM generate_series(1, 100)",
        "SELECT dblink('x', 'delete from orders')",
        "SELECT 'x'::regclass",
        "WITH RECURSIVE x AS (SELECT 1 UNION ALL SELECT * FROM x) SELECT * FROM x",
        "SELECT * FROM stolen WHERE EXISTS (WITH stolen AS (SELECT 1) SELECT * FROM stolen)",
        "SELECT * FROM orders LIMIT :limit",
    ],
)
def test_deny_dangerous_or_unreviewed_sql(dialect: str, schema: str, sql: str) -> None:
    policy = SQLPolicy(dialect, schema, frozenset({"orders"}))
    with pytest.raises(AnalyticsError):
        policy.prepare(QueryRequest(sql=sql))


@pytest.mark.parametrize("dialect,schema", [("postgres", "public"), ("teradata", "retail")])
def test_ast_binding_and_cte_scope(dialect: str, schema: str) -> None:
    policy = SQLPolicy(dialect, schema, frozenset({"orders"}))
    attack = "USD'; DELETE FROM orders;--"
    prepared = policy.prepare(
        QueryRequest(
            sql="""
        WITH source AS (SELECT * FROM orders WHERE currency = :currency)
        SELECT COUNT(*) FROM source
    """,
            parameters={"currency": attack},
            row_limit=5,
        )
    )
    assert attack not in prepared.sql
    assert attack in (
        prepared.parameters.values()
        if isinstance(prepared.parameters, dict)
        else prepared.parameters
    )
    assert schema in prepared.sql
    assert "6" in prepared.sql
    # Duplicate named bindings are correctly repeated for the positional Teradata driver.
    repeated = policy.prepare(QueryRequest(sql="SELECT :v + :v FROM orders", parameters={"v": 4}))
    assert repeated.parameters == ({"v": 4} if dialect == "postgres" else [4, 4])


def test_parameter_mismatch_and_literal_percent() -> None:
    policy = SQLPolicy("postgres", "public", frozenset({"orders"}))
    for parameters in [{}, {"v": 1, "extra": 2}]:
        with pytest.raises(AnalyticsError):
            policy.prepare(QueryRequest(sql="SELECT :v FROM orders", parameters=parameters))
    result = policy.prepare(
        QueryRequest(
            sql="SELECT '100% %(v)s' FROM orders WHERE currency = :v", parameters={"v": "USD"}
        )
    )
    assert "'100%% %%(v)s'" in result.sql
    assert result.sql.count("%(v)s") == 2  # one escaped literal, one actual binding


def test_teradata_cte_bindings_follow_generated_sql_order() -> None:
    policy = SQLPolicy("teradata", "retail", frozenset({"orders"}))
    query = QueryRequest(
        sql="WITH x AS (SELECT :first AS a FROM orders) SELECT :second FROM x",
        parameters={"first": 11, "second": 22},
    )
    prepared = policy.prepare(query)
    assert prepared.parameters == [11, 22]
    assert prepared.sql.count("?") == 2
