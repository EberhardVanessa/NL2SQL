from __future__ import annotations

import os
import sqlite3

from table_schema import generate_schema_prompt


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


def validate_sql_syntax(sql: str, dialect: str = "postgres") -> tuple[bool, str | None]:
    try:
        import sqlglot

        sqlglot.parse_one(sql, read=dialect)
        return True, None
    except ModuleNotFoundError:
        return True, None
    except Exception as e:
        return False, str(e)


def validate_sql_execution_postgresql(sql: str) -> tuple[bool, str | None]:
    try:
        conn = connect_postgresql()
        cur = conn.cursor()
        cur.execute(f"EXPLAIN {sql}")
        cur.fetchall()
        cur.close()
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)


def _sqlite_path_from_schema_path(schema_path: str) -> str:
    db_dir = os.path.dirname(os.path.normpath(schema_path))
    db_id = os.path.basename(db_dir)
    return os.path.join(db_dir, f"{db_id}.sqlite")


def _connect_sqlite(db_path: str):
    normalized = os.path.normpath(db_path)
    if os.path.basename(normalized).lower() == "schema.sql":
        sqlite_path = _sqlite_path_from_schema_path(normalized)
        if os.path.exists(sqlite_path):
            return sqlite3.connect(sqlite_path)

        conn = sqlite3.connect(":memory:")
        conn.executescript(generate_schema_prompt(normalized))
        return conn

    return sqlite3.connect(normalized)


def validate_sql_execution_sqlite(sql: str, db_path: str | None) -> tuple[bool, str | None]:
    if not db_path:
        return False, "SQLite execution validation requires db_path"
    try:
        conn = _connect_sqlite(db_path)
        cur = conn.cursor()
        cur.execute(f"EXPLAIN QUERY PLAN {sql}")
        cur.fetchall()
        cur.close()
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)


def validate_sql(sql: str, db_path: str | None = None, dialect: str = "postgres") -> dict[str, object]:
    normalized_dialect = str(dialect or "postgres").lower()
    sqlglot_dialect = "postgres" if normalized_dialect in {"postgresql", "postgres"} else normalized_dialect
    syntax_ok, syntax_error = validate_sql_syntax(sql, sqlglot_dialect)
    if not syntax_ok:
        return {
            "syntax_ok": False,
            "syntax_error": syntax_error,
            "execution_ok": False,
            "execution_error": None,
        }

    if sqlglot_dialect == "sqlite":
        execution_ok, execution_error = validate_sql_execution_sqlite(sql, db_path)
    else:
        execution_ok, execution_error = validate_sql_execution_postgresql(sql)
    return {
        "syntax_ok": True,
        "syntax_error": None,
        "execution_ok": execution_ok,
        "execution_error": execution_error,
    }
