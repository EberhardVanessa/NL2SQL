from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


DEFAULT_DEBUG_PATH = "../llm/exp_result/qwen/bird/predict_mini_dev_Qwen2.5-Coder-7B-Instruct-linking_PostgreSQL_debug.json"
DEFAULT_SPIDER_DB_ROOT = "../../../spider/spider_data/database"
DEFAULT_FAILURE_PATH = "eval_failures.json"


def normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def normalize_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    normalized = [tuple(normalize_value(value) for value in row) for row in rows]
    try:
        return sorted(normalized, key=lambda row: json.dumps(make_json_serializable(row), sort_keys=True))
    except TypeError:
        return normalized


def compare_scalar(left: Any, right: Any, tol: float) -> bool:
    left = normalize_value(left)
    right = normalize_value(right)

    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=tol, abs_tol=tol)

    return left == right


def compare_rows(rows1: list[tuple[Any, ...]] | None, rows2: list[tuple[Any, ...]] | None, tol: float = 1e-6) -> bool:
    if rows1 is None or rows2 is None:
        return False

    rows1 = normalize_rows(rows1)
    rows2 = normalize_rows(rows2)

    if len(rows1) != len(rows2):
        return False

    for row1, row2 in zip(rows1, rows2):
        if len(row1) != len(row2):
            return False
        if any(not compare_scalar(value1, value2, tol) for value1, value2 in zip(row1, row2)):
            return False

    return True


def make_json_serializable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (date, datetime, time)):
        return obj.isoformat()
    if isinstance(obj, tuple):
        return [make_json_serializable(x) for x in obj]
    if isinstance(obj, list):
        return [make_json_serializable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): make_json_serializable(v) for k, v in obj.items()}
    return obj


def strip_sql(sql: Any) -> str:
    if not isinstance(sql, str):
        return ""
    return sql.strip().rstrip(";").strip()


def resolve_dataset(row: dict[str, Any], default_dataset: str) -> str:
    dataset = row.get("dataset") or default_dataset
    return str(dataset).lower()


def spider_db_path(db_root_path: str, db_id: str) -> str:
    return os.path.join(db_root_path, db_id, f"{db_id}.sqlite")


def connect_postgres(args: argparse.Namespace):
    import psycopg2

    conn = psycopg2.connect(
        dbname=args.pgdatabase,
        user=args.pguser,
        password=args.pgpassword,
        host=args.pghost,
        port=args.pgport,
    )
    conn.autocommit = True
    return conn


def fetch_all_or_empty(cursor: Any, db_kind: str) -> list[tuple[Any, ...]]:
    try:
        return cursor.fetchall()
    except Exception as exc:
        if db_kind == "postgres":
            import psycopg2

            if isinstance(exc, psycopg2.ProgrammingError):
                return []
        if db_kind == "sqlite" and "no results to fetch" in str(exc).lower():
            return []
        raise


def run_sql_postgres(conn: Any, sql: str) -> tuple[bool, list[tuple[Any, ...]] | None, str | None]:
    if not sql:
        return False, None, "empty SQL"

    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            return True, normalize_rows(fetch_all_or_empty(cur, "postgres")), None
    except Exception as exc:
        conn.rollback()
        return False, None, str(exc)


def run_sql_sqlite(db_path: str, sql: str) -> tuple[bool, list[tuple[Any, ...]] | None, str | None]:
    if not sql:
        return False, None, "empty SQL"
    if not os.path.exists(db_path):
        return False, None, f"SQLite database not found: {db_path}"

    try:
        with closing(sqlite3.connect(db_path)) as conn:
            cur = conn.cursor()
            cur.execute(sql)
            return True, normalize_rows(fetch_all_or_empty(cur, "sqlite")), None
    except Exception as exc:
        return False, None, str(exc)


def run_sql(
    dataset: str,
    sql: str,
    db_id: str,
    pg_conn: Any,
    spider_db_root_path: str,
) -> tuple[bool, list[tuple[Any, ...]] | None, str | None]:
    if dataset == "spider":
        return run_sql_sqlite(spider_db_path(spider_db_root_path, db_id), sql)
    return run_sql_postgres(pg_conn, sql)


def load_debug_rows(path: str, limit: int | None) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    if not isinstance(rows, list):
        raise ValueError(f"Expected debug JSON list, got {type(rows).__name__}")
    if limit is not None:
        rows = rows[:limit]
    return rows


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    rows = load_debug_rows(args.debug_path, args.limit)

    postgres_needed = any(resolve_dataset(row, args.dataset) != "spider" for row in rows)
    pg_conn = connect_postgres(args) if postgres_needed else None

    overall_correct = 0
    overall_total = 0
    valid_sql = 0
    gold_valid_sql = 0

    bucket_total = defaultdict(int)
    bucket_correct = defaultdict(int)
    dataset_total = defaultdict(int)
    dataset_correct = defaultdict(int)

    failures = []

    try:
        for fallback_index, row in enumerate(rows):
            index = row.get("index", fallback_index)
            dataset = resolve_dataset(row, args.dataset)
            db_id = str(row.get("db_id") or "")
            question = row.get("question", "")
            difficulty = str(row.get("difficulty") or "unknown")
            gold_sql = strip_sql(row.get("gold_sql"))
            pred_sql = strip_sql(row.get("final_sql"))

            overall_total += 1
            bucket_total[difficulty] += 1
            dataset_total[dataset] += 1

            if not db_id:
                failures.append(
                    {
                        "index": index,
                        "dataset": dataset,
                        "question": question,
                        "reason": "missing db_id",
                    }
                )
                continue

            if not gold_sql:
                failures.append(
                    {
                        "index": index,
                        "dataset": dataset,
                        "db_id": db_id,
                        "question": question,
                        "pred_sql": pred_sql,
                        "reason": "missing gold_sql in debug row",
                    }
                )
                continue

            gold_ok, gold_rows, gold_err = run_sql(dataset, gold_sql, db_id, pg_conn, args.spider_db_root_path)
            pred_ok, pred_rows, pred_err = run_sql(dataset, pred_sql, db_id, pg_conn, args.spider_db_root_path)

            if gold_ok:
                gold_valid_sql += 1
            if pred_ok:
                valid_sql += 1

            if gold_ok and pred_ok and compare_rows(gold_rows, pred_rows, args.tolerance):
                overall_correct += 1
                bucket_correct[difficulty] += 1
                dataset_correct[dataset] += 1
            else:
                failures.append(
                    {
                        "index": index,
                        "dataset": dataset,
                        "db_id": db_id,
                        "difficulty": difficulty,
                        "question": question,
                        "gold_sql": gold_sql,
                        "pred_sql": pred_sql,
                        "gold_ok": gold_ok,
                        "pred_ok": pred_ok,
                        "gold_error": gold_err,
                        "pred_error": pred_err,
                        "gold_rows": gold_rows,
                        "pred_rows": pred_rows,
                    }
                )
    finally:
        if pg_conn is not None:
            pg_conn.close()

    return {
        "overall_correct": overall_correct,
        "overall_total": overall_total,
        "valid_sql": valid_sql,
        "gold_valid_sql": gold_valid_sql,
        "bucket_total": dict(bucket_total),
        "bucket_correct": dict(bucket_correct),
        "dataset_total": dict(dataset_total),
        "dataset_correct": dict(dataset_correct),
        "failures": failures,
    }


def print_report(result: dict[str, Any]) -> None:
    overall_total = result["overall_total"]
    overall_correct = result["overall_correct"]
    valid_sql = result["valid_sql"]
    gold_valid_sql = result["gold_valid_sql"]

    print("\n=== Execution Accuracy ===")
    overall_acc = 100 * overall_correct / overall_total if overall_total else 0.0
    valid_acc = 100 * valid_sql / overall_total if overall_total else 0.0
    gold_valid_acc = 100 * gold_valid_sql / overall_total if overall_total else 0.0
    print(f"Overall: {overall_correct}/{overall_total} = {overall_acc:.2f}%")
    print(f"Valid predicted SQL: {valid_sql}/{overall_total} = {valid_acc:.2f}%")
    print(f"Valid gold SQL: {gold_valid_sql}/{overall_total} = {gold_valid_acc:.2f}%")

    for dataset in sorted(result["dataset_total"]):
        total = result["dataset_total"][dataset]
        correct = result["dataset_correct"].get(dataset, 0)
        acc = 100 * correct / total if total else 0.0
        print(f"{dataset}: {correct}/{total} = {acc:.2f}%")

    if result["bucket_total"]:
        print("\n=== By Difficulty ===")
        for difficulty in sorted(result["bucket_total"]):
            total = result["bucket_total"][difficulty]
            correct = result["bucket_correct"].get(difficulty, 0)
            acc = 100 * correct / total if total else 0.0
            print(f"{difficulty}: {correct}/{total} = {acc:.2f}%")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate text-to-SQL debug JSON using Postgres for BIRD and SQLite for Spider."
    )
    parser.add_argument("--debug_path", default=DEFAULT_DEBUG_PATH)
    parser.add_argument("--dataset", choices=["bird", "spider"], default="bird")
    parser.add_argument("--spider_db_root_path", default=DEFAULT_SPIDER_DB_ROOT)
    parser.add_argument("--failure_path", default=DEFAULT_FAILURE_PATH)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--tolerance", type=float, default=1e-6)
    parser.add_argument("--pghost", default=os.getenv("PGHOST", "localhost"))
    parser.add_argument("--pgport", type=int, default=int(os.getenv("PGPORT", "5432")))
    parser.add_argument("--pgdatabase", default=os.getenv("PGDATABASE", "BIRD"))
    parser.add_argument("--pguser", default=os.getenv("PGUSER", "bird"))
    parser.add_argument("--pgpassword", default=os.getenv("PGPASSWORD", "birdpass"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = evaluate(args)
    print_report(result)

    with open(args.failure_path, "w", encoding="utf-8") as f:
        json.dump(make_json_serializable(result["failures"]), f, indent=2, ensure_ascii=False)

    print(f"\nSaved detailed failures to {args.failure_path}")


if __name__ == "__main__":
    main()
