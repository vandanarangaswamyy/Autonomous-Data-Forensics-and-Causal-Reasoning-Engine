"""Fail-closed SQL AST subset. Database privileges remain an independent boundary."""

from dataclasses import dataclass
from typing import Any, cast
from uuid import uuid4

import sqlglot
from atlas_contracts.analytics import AnalyticsError, QueryRequest, TableRef
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

# Deliberately narrow: unknown nodes/functions require review before admission.
ALLOWED_NODES = frozenset(
    {
        "TableAlias",
        "Select",
        "Union",
        "Intersect",
        "Except",
        "Subquery",
        "CTE",
        "With",
        "From",
        "Table",
        "Column",
        "Identifier",
        "Star",
        "Alias",
        "Where",
        "Group",
        "Having",
        "Order",
        "Ordered",
        "Limit",
        "Offset",
        "Join",
        "Distinct",
        "Literal",
        "Null",
        "Boolean",
        "Placeholder",
        "And",
        "Or",
        "Not",
        "EQ",
        "NEQ",
        "GT",
        "GTE",
        "LT",
        "LTE",
        "Is",
        "In",
        "Between",
        "Paren",
        "Add",
        "Sub",
        "Mul",
        "Div",
        "Mod",
        "Neg",
        "Count",
        "Sum",
        "Avg",
        "Min",
        "Max",
        "Coalesce",
        "Round",
        "Abs",
        "Lower",
        "Upper",
        "Length",
        "Case",
        "If",
        "Like",
        "ILike",
        "Escape",
        "Cast",
        "DataType",
        "DataTypeParam",
        "Tuple",
        "Exists",
        "Nullif",
    }
)
SAFE_TYPES = frozenset(
    {
        "INT",
        "BIGINT",
        "SMALLINT",
        "DECIMAL",
        "DOUBLE",
        "FLOAT",
        "BOOLEAN",
        "TEXT",
        "VARCHAR",
        "CHAR",
        "DATE",
        "TIMESTAMP",
        "TIMESTAMPTZ",
    }
)


@dataclass(frozen=True)
class PreparedQuery:
    sql: str
    parameters: dict[str, Any] | list[Any]


class SQLPolicy:
    def __init__(self, dialect: str, schema: str, tables: frozenset[str]) -> None:
        self.dialect, self.schema, self.tables = dialect, schema, tables

    def check_table(self, table: TableRef) -> None:
        if table.schema_name != self.schema or table.table not in self.tables:
            raise AnalyticsError("table_denied", "Dataset is outside the authorized catalog")

    def prepare(self, request: QueryRequest, *, explain: bool = False) -> PreparedQuery:
        try:
            statements = sqlglot.parse(request.sql, read=self.dialect, error_level=ErrorLevel.RAISE)
            if len(statements) != 1 or not isinstance(
                statements[0], (exp.Select, exp.SetOperation)
            ):
                raise AnalyticsError("sql_denied", "Exactly one SELECT query is required")
            tree = statements[0]
            nodes = list(tree.walk())
            if len(nodes) > 800:
                raise AnalyticsError("sql_denied", "Query is too complex")
            for node in nodes:
                if type(node).__name__ not in ALLOWED_NODES:
                    raise AnalyticsError("sql_denied", "SQL construct is not permitted")
                if isinstance(node, exp.With) and node.args.get("recursive"):
                    raise AnalyticsError("sql_denied", "Recursive queries are not permitted")
                if isinstance(node, exp.DataType) and node.this.value not in SAFE_TYPES:
                    raise AnalyticsError("sql_denied", "Cast type is not permitted")
                if isinstance(node, (exp.Limit, exp.Offset)) and not isinstance(
                    node.expression, exp.Literal
                ):
                    raise AnalyticsError(
                        "sql_denied", "Limits and offsets must be integer literals"
                    )
                if isinstance(node, exp.Select) and (
                    node.args.get("locks") or node.args.get("into")
                ):
                    raise AnalyticsError("sql_denied", "Locks and SELECT INTO are not permitted")
            # Scope resolution prevents CTE alias tricks from hiding physical source tables.
            for scope in traverse_scope(tree):
                for _, source in scope.selected_sources.values():
                    if isinstance(source, Scope):
                        continue
                    if not isinstance(source, exp.Table) or not isinstance(
                        source.this, exp.Identifier
                    ):
                        raise AnalyticsError(
                            "sql_denied", "Only approved physical tables are permitted"
                        )
                    if source.catalog or (source.db and source.db != self.schema):
                        raise AnalyticsError(
                            "table_denied", "Cross-catalog access is not permitted"
                        )
                    self.check_table(TableRef(schema_name=self.schema, table=source.name))
                    source.set("db", exp.to_identifier(self.schema, quoted=True))
            parameters = list(tree.find_all(exp.Placeholder))
            names = [str(p.this) for p in parameters]
            if any(not isinstance(p.this, str) or not p.this.isidentifier() for p in parameters):
                raise AnalyticsError("invalid_parameters", "Use named :parameter bindings")
            if set(names) != set(request.parameters):
                raise AnalyticsError(
                    "invalid_parameters", "Parameters must exactly match query bindings"
                )
            original_limit = tree.args.get("limit")
            maximum = request.row_limit + 1
            if original_limit:
                maximum = min(maximum, int(original_limit.expression.this))
            if maximum < 0:
                raise AnalyticsError("sql_denied", "Negative row limits are not permitted")
            tree = tree.limit(maximum, copy=False)
            # Emit opaque markers, then bind in generated SQL order. AST traversal order
            # differs from SQL order for CTEs and some dialect rewrites.
            markers: dict[str, str] = {}

            def placeholder(node: exp.Expression) -> exp.Expression:
                if isinstance(node, exp.Placeholder):
                    marker = "atlas_bind_" + uuid4().hex
                    while marker in request.sql or marker in self.schema or marker in self.tables:
                        marker = "atlas_bind_" + uuid4().hex
                    markers[marker] = str(node.this)
                    return exp.Var(this=marker)
                return node

            tree = cast(exp.Select | exp.SetOperation, tree.transform(placeholder, copy=False))
            statement = tree.sql(dialect=self.dialect, unsupported_level=ErrorLevel.RAISE)
            bindings: dict[str, Any] | list[Any]
            if self.dialect == "postgres":
                bindings = dict(request.parameters)
                statement = statement.replace("%", "%%")
                for marker, name in markers.items():
                    statement = statement.replace(marker, f"%({name})s")
            else:
                positions: list[tuple[int, str]] = []
                for marker, name in markers.items():
                    offset = statement.find(marker)
                    while offset != -1:
                        positions.append((offset, name))
                        offset = statement.find(marker, offset + len(marker))
                bindings = [request.parameters[name] for _, name in sorted(positions)]
                for marker in markers:
                    statement = statement.replace(marker, "?")
            if explain:
                statement = (
                    "EXPLAIN (FORMAT JSON, ANALYZE FALSE) "
                    if self.dialect == "postgres"
                    else "EXPLAIN "
                ) + statement
            return PreparedQuery(statement, bindings)
        except AnalyticsError:
            raise
        except (SqlglotError, ValueError, TypeError, AttributeError) as exc:
            raise AnalyticsError("sql_denied", "SQL could not be safely parsed") from exc
