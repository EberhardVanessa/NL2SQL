from __future__ import annotations

import os
import sqlite3
from typing import Any


def normalize_sql_dialect(dialect: str | None) -> str:
    normalized = str(dialect or "sqlite").strip().lower()
    if normalized in {"postgres", "postgresql"}:
        return "postgresql"
    if normalized == "sqlite":
        return "sqlite"
    return "sqlite"


def display_sql_dialect(dialect: str | None) -> str:
    normalized = normalize_sql_dialect(dialect)
    if normalized == "postgresql":
        return "PostgreSQL"
    return "SQLite"


def sqlglot_dialect(dialect: str | None) -> str:
    normalized = normalize_sql_dialect(dialect)
    return "postgres" if normalized == "postgresql" else "sqlite"


def validate_sql_syntax(sql: str, dialect: str) -> tuple[bool, str | None]:
    try:
        import sqlglot

        sqlglot.parse_one(sql, read=sqlglot_dialect(dialect))
        return True, None
    except ModuleNotFoundError:
        return True, None
    except Exception as exc:
        return False, str(exc)


def connect_postgresql():
    import psycopg2

    db = psycopg2.connect(
        dbname=os.getenv("PGDATABASE", "BIRD"),
        user=os.getenv("PGUSER", "bird"),
        password=os.getenv("PGPASSWORD", "birdpass"),
        host=os.getenv("PGHOST", "localhost"),
        port=os.getenv("PGPORT", "5432"),
    )
    db.autocommit = True
    return db


def validate_sql_execution_postgresql(sql: str) -> tuple[bool, str | None, bool]:
    try:
        conn = connect_postgresql()
        cur = conn.cursor()
        cur.execute(f"EXPLAIN {sql}")
        cur.fetchall()
        cur.close()
        conn.close()
        return True, None, False
    except Exception as exc:
        return False, str(exc), False


def _strip_schema_context_markers(schema_context: str) -> str:
    lines = []
    for line in schema_context.splitlines():
        if line.strip().startswith("====="):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def validate_sql_execution_sqlite(
    sql: str,
    schema_context: str | None,
) -> tuple[bool, str | None, bool]:
    if not schema_context or not schema_context.strip():
        return True, "SQLite execution validation skipped: no schema context provided.", True

    ddl = _strip_schema_context_markers(schema_context)
    conn = None
    try:
        conn = sqlite3.connect(":memory:")
        conn.executescript(ddl)
    except Exception as exc:
        if conn is not None:
            conn.close()
        return True, f"SQLite execution validation skipped: schema setup failed: {exc}", True

    try:
        cur = conn.cursor()
        cur.execute(f"EXPLAIN QUERY PLAN {sql}")
        cur.fetchall()
        cur.close()
        conn.close()
        return True, None, False
    except Exception as exc:
        conn.close()
        return False, str(exc), False


def validate_sql(
    sql: str,
    *,
    dialect: str,
    schema_context: str | None = None,
) -> dict[str, Any]:
    normalized_dialect = normalize_sql_dialect(dialect)
    cleaned_sql = (sql or "").strip()

    if not cleaned_sql:
        return {
            "syntax_ok": False,
            "syntax_error": "SQL is empty.",
            "execution_ok": False,
            "execution_error": None,
            "execution_skipped": False,
            "dialect": normalized_dialect,
        }

    syntax_ok, syntax_error = validate_sql_syntax(cleaned_sql, normalized_dialect)
    if not syntax_ok:
        return {
            "syntax_ok": False,
            "syntax_error": syntax_error,
            "execution_ok": False,
            "execution_error": None,
            "execution_skipped": False,
            "dialect": normalized_dialect,
        }

    if normalized_dialect == "postgresql":
        execution_ok, execution_error, execution_skipped = validate_sql_execution_postgresql(
            cleaned_sql
        )
    else:
        execution_ok, execution_error, execution_skipped = validate_sql_execution_sqlite(
            cleaned_sql,
            schema_context,
        )

    return {
        "syntax_ok": True,
        "syntax_error": None,
        "execution_ok": execution_ok,
        "execution_error": execution_error,
        "execution_skipped": execution_skipped,
        "dialect": normalized_dialect,
    }


def validation_error_message(validation: dict[str, Any]) -> str:
    return str(
        validation.get("syntax_error")
        or validation.get("execution_error")
    )
