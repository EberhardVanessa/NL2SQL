from __future__ import annotations

import argparse
import csv
import json
import os
from collections import defaultdict
from typing import Any, Optional

try:
    import sqlglot
    from sqlglot import exp
except ImportError:
    sqlglot = None
    exp = None


def _norm_identifier(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().strip("`\"[]")
    text = text.replace("`", "").replace('"', "")
    return text.lower()


def _norm_column(value: Any) -> str:
    raw = _norm_identifier(value)
    if not raw or "." not in raw:
        return ""
    parts = [p for p in raw.split(".") if p]
    if len(parts) < 2:
        return ""
    return f"{parts[-2]}.{parts[-1]}"


def _safe_set(value: Any, column_like: bool = False) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, set):
        iterable = value
    elif isinstance(value, (list, tuple)):
        iterable = value
    else:
        iterable = [value]
    out = set()
    for item in iterable:
        normalized = _norm_column(item) if column_like else _norm_identifier(item)
        if normalized:
            out.add(normalized)
    return out


def _build_schema_index(schema: dict[str, Any]) -> tuple[dict[str, set[str]], int, int]:
    tables = schema.get("tables", {}) if isinstance(schema.get("tables"), dict) else {}
    table_cols: dict[str, set[str]] = {}
    total_columns = 0
    for raw_table, table_data in tables.items():
        table_name = _norm_identifier(raw_table)
        if not table_name or not isinstance(table_data, dict):
            continue
        raw_columns = table_data.get("columns", {})
        if not isinstance(raw_columns, dict):
            raw_columns = {}
        cols = {_norm_identifier(c) for c in raw_columns if _norm_identifier(c)}
        table_cols[table_name] = cols
        total_columns += len(cols)
    return table_cols, len(table_cols), total_columns


def _extract_cte_names(root_expr: Any) -> set[str]:
    names: set[str] = set()
    if exp is None or root_expr is None:
        return names
    for cte in root_expr.find_all(exp.CTE):
        name = _norm_identifier(getattr(cte, "alias_or_name", ""))
        if name:
            names.add(name)
    return names


def extract_required_schema_from_gold_sql(
    gold_sql: str,
    schema: dict[str, Any],
    dialect: str = "sqlite",
) -> dict[str, Any]:
    """
    Extract required schema elements from gold SQL.

    If sqlglot is not installed, returns an error in the output dict.
    """
    if sqlglot is None or exp is None:
        return {
            "tables": set(),
            "columns": set(),
            "ambiguous_columns": set(),
            "unresolved_columns": set(),
            "error": "sqlglot_not_installed",
        }

    table_columns, _, _ = _build_schema_index(schema)
    used_tables: set[str] = set()
    used_columns: set[str] = set()
    ambiguous_columns: set[str] = set()
    unresolved_columns: set[str] = set()

    try:
        root = sqlglot.parse_one(gold_sql, read=dialect)
    except Exception as ex:
        return {
            "tables": set(),
            "columns": set(),
            "ambiguous_columns": set(),
            "unresolved_columns": set(),
            "error": f"sql_parse_error: {ex}",
        }

    cte_names = _extract_cte_names(root)
    alias_to_table: dict[str, str] = {}

    # Collect real schema tables from all query levels (including subqueries and CTE bodies).
    for table_node in root.find_all(exp.Table):
        table_name = _norm_identifier(getattr(table_node, "name", ""))
        alias_name = _norm_identifier(getattr(table_node, "alias_or_name", ""))
        if not table_name:
            continue
        if table_name in cte_names:
            if alias_name:
                alias_to_table[alias_name] = table_name
            continue
        if table_name in table_columns:
            used_tables.add(table_name)
            alias_to_table[table_name] = table_name
            if alias_name:
                alias_to_table[alias_name] = table_name

    # Extract columns used across SELECT/WHERE/JOIN/ON/GROUP/HAVING/ORDER/subqueries/CTEs.
    for col in root.find_all(exp.Column):
        col_name = _norm_identifier(getattr(col, "name", ""))
        table_ref = _norm_identifier(getattr(col, "table", ""))

        if col_name == "*":
            # For SELECT *, table coverage is already represented by used_tables.
            if table_ref:
                resolved = alias_to_table.get(table_ref, table_ref)
                if resolved in table_columns:
                    used_tables.add(resolved)
            continue

        if table_ref:
            resolved_table = alias_to_table.get(table_ref, table_ref)
            if resolved_table in table_columns:
                used_tables.add(resolved_table)
                if col_name and col_name in table_columns[resolved_table]:
                    used_columns.add(f"{resolved_table}.{col_name}")
                elif col_name:
                    unresolved_columns.add(f"{table_ref}.{col_name}")
            elif col_name:
                unresolved_columns.add(f"{table_ref}.{col_name}")
            continue

        if not col_name:
            continue
        matches = [t for t in used_tables if col_name in table_columns.get(t, set())]
        if len(matches) == 1:
            used_columns.add(f"{matches[0]}.{col_name}")
        elif len(matches) > 1:
            ambiguous_columns.add(col_name)
        else:
            unresolved_columns.add(col_name)

    return {
        "tables": used_tables,
        "columns": used_columns,
        "ambiguous_columns": ambiguous_columns,
        "unresolved_columns": unresolved_columns,
        "error": None,
    }


def _precision(predicted: set[str], gold: set[str]) -> float:
    if not predicted:
        return 1.0 if not gold else 0.0
    return len(predicted & gold) / len(predicted)


def _recall(predicted: set[str], gold: set[str]) -> float:
    if not gold:
        return 1.0
    return len(predicted & gold) / len(gold)


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def evaluate_schema_linking(
    predicted_tables: set[str],
    predicted_columns: set[str],
    gold_tables: set[str],
    gold_columns: set[str],
    total_tables: int,
    total_columns: int,
) -> dict[str, Any]:
    """Compute schema-linking metrics for one prediction."""
    predicted_tables = _safe_set(predicted_tables)
    predicted_columns = _safe_set(predicted_columns, column_like=True)
    gold_tables = _safe_set(gold_tables)
    gold_columns = _safe_set(gold_columns, column_like=True)

    table_precision = _precision(predicted_tables, gold_tables)
    table_recall = _recall(predicted_tables, gold_tables)
    column_precision = _precision(predicted_columns, gold_columns)
    column_recall = _recall(predicted_columns, gold_columns)

    missing_tables = gold_tables - predicted_tables
    missing_columns = gold_columns - predicted_columns
    extra_tables = predicted_tables - gold_tables
    extra_columns = predicted_columns - gold_columns

    table_keep_ratio = len(predicted_tables) / total_tables if total_tables > 0 else 0.0
    column_keep_ratio = len(predicted_columns) / total_columns if total_columns > 0 else 0.0

    return {
        "table_recall": table_recall,
        "column_recall": column_recall,
        "table_precision": table_precision,
        "column_precision": column_precision,
        "table_f1": _f1(table_precision, table_recall),
        "column_f1": _f1(column_precision, column_recall),
        "table_exact_coverage": gold_tables.issubset(predicted_tables),
        "column_exact_coverage": gold_columns.issubset(predicted_columns),
        "table_keep_ratio": table_keep_ratio,
        "column_keep_ratio": column_keep_ratio,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "extra_tables": extra_tables,
        "extra_columns": extra_columns,
        "gold_table_count": len(gold_tables),
        "gold_column_count": len(gold_columns),
        "predicted_table_count": len(predicted_tables),
        "predicted_column_count": len(predicted_columns),
    }


def _extract_prediction_for_method(linking_result: dict[str, Any], method: str) -> dict[str, set[str]]:
    entry = linking_result.get(method)
    if not isinstance(entry, dict):
        return {"tables": set(), "columns": set()}
    return {
        "tables": _safe_set(entry.get("tables")),
        "columns": _safe_set(entry.get("columns"), column_like=True),
    }


def evaluate_linking_result_against_gold(
    linking_result: dict[str, Any],
    gold_sql: str,
    schema: dict[str, Any],
    dialect: str = "sqlite",
) -> dict[str, Any]:
    """Evaluate per-method predictions against required schema extracted from gold SQL."""
    method_source = linking_result
    if isinstance(linking_result.get("linking_result"), dict):
        method_source = linking_result["linking_result"]

    gold_required = extract_required_schema_from_gold_sql(gold_sql=gold_sql, schema=schema, dialect=dialect)
    table_columns, total_tables, total_columns = _build_schema_index(schema)
    _ = table_columns

    methods: dict[str, dict[str, set[str]]] = {}
    available_methods = ["ddl", "m_schema", "mac_schema", "union", "intersection"]
    for method in available_methods:
        prediction = _extract_prediction_for_method(method_source, method)
        if prediction["tables"] or prediction["columns"] or method in method_source:
            methods[method] = prediction

    if not methods and ("predicted_tables" in linking_result or "predicted_columns" in linking_result):
        methods["direct"] = {
            "tables": _safe_set(linking_result.get("predicted_tables")),
            "columns": _safe_set(linking_result.get("predicted_columns"), column_like=True),
        }

    evaluations: dict[str, dict[str, Any]] = {}
    for method, pred in methods.items():
        metrics = evaluate_schema_linking(
            predicted_tables=pred["tables"],
            predicted_columns=pred["columns"],
            gold_tables=gold_required["tables"],
            gold_columns=gold_required["columns"],
            total_tables=total_tables,
            total_columns=total_columns,
        )
        metrics["parse_error"] = gold_required["error"]
        metrics["ambiguous_columns"] = set(gold_required["ambiguous_columns"])
        metrics["unresolved_columns"] = set(gold_required["unresolved_columns"])
        evaluations[method] = metrics

    return {
        "gold_required": gold_required,
        "methods": evaluations,
        "total_tables": total_tables,
        "total_columns": total_columns,
        "dialect": dialect,
    }


def aggregate_schema_linking_metrics(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Aggregate per-example evaluation results by method."""
    bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        for method_name, metrics in result.get("methods", {}).items():
            bucket[method_name].append(metrics)

    output: dict[str, dict[str, Any]] = {}
    for method_name, items in bucket.items():
        n = len(items)
        if n == 0:
            continue

        def mean(key: str) -> float:
            return sum(float(item.get(key, 0.0)) for item in items) / n

        output[method_name] = {
            "mean_table_recall": mean("table_recall"),
            "mean_column_recall": mean("column_recall"),
            "mean_table_precision": mean("table_precision"),
            "mean_column_precision": mean("column_precision"),
            "mean_table_f1": mean("table_f1"),
            "mean_column_f1": mean("column_f1"),
            "table_exact_coverage_rate": sum(bool(i.get("table_exact_coverage")) for i in items) / n,
            "column_exact_coverage_rate": sum(bool(i.get("column_exact_coverage")) for i in items) / n,
            "mean_table_keep_ratio": mean("table_keep_ratio"),
            "mean_column_keep_ratio": mean("column_keep_ratio"),
            "count_examples": n,
            "count_parse_failures": sum(1 for i in items if i.get("parse_error")),
            "count_examples_with_ambiguous_columns": sum(1 for i in items if i.get("ambiguous_columns")),
            "count_examples_with_unresolved_columns": sum(1 for i in items if i.get("unresolved_columns")),
        }
    return output


def _set_to_csv_text(values: Any) -> str:
    if values is None:
        return ""
    if isinstance(values, set):
        items = sorted(values)
    elif isinstance(values, (list, tuple)):
        items = sorted(str(v) for v in values)
    else:
        items = [str(values)]
    return ";".join(str(v) for v in items)


def build_diagnostic_row(
    question_id: str,
    question: str,
    db_id: str,
    gold_sql: str,
    method_name: str,
    metrics: dict[str, Any],
    gold_required: dict[str, Any],
    predicted: dict[str, Any],
) -> dict[str, Any]:
    """Build one CSV-friendly diagnostic row for a question/method pair."""
    return {
        "question_id": question_id,
        "db_id": db_id,
        "method_name": method_name,
        "question": question,
        "gold_sql": gold_sql,
        "gold_tables": _set_to_csv_text(gold_required.get("tables", set())),
        "gold_columns": _set_to_csv_text(gold_required.get("columns", set())),
        "predicted_tables": _set_to_csv_text(predicted.get("tables", set())),
        "predicted_columns": _set_to_csv_text(predicted.get("columns", set())),
        "missing_tables": _set_to_csv_text(metrics.get("missing_tables", set())),
        "missing_columns": _set_to_csv_text(metrics.get("missing_columns", set())),
        "extra_tables": _set_to_csv_text(metrics.get("extra_tables", set())),
        "extra_columns": _set_to_csv_text(metrics.get("extra_columns", set())),
        "ambiguous_columns": _set_to_csv_text(gold_required.get("ambiguous_columns", set())),
        "unresolved_columns": _set_to_csv_text(gold_required.get("unresolved_columns", set())),
        "table_recall": float(metrics.get("table_recall", 0.0)),
        "column_recall": float(metrics.get("column_recall", 0.0)),
        "table_precision": float(metrics.get("table_precision", 0.0)),
        "column_precision": float(metrics.get("column_precision", 0.0)),
        "table_f1": float(metrics.get("table_f1", 0.0)),
        "column_f1": float(metrics.get("column_f1", 0.0)),
        "table_exact_coverage": bool(metrics.get("table_exact_coverage", False)),
        "column_exact_coverage": bool(metrics.get("column_exact_coverage", False)),
        "table_keep_ratio": float(metrics.get("table_keep_ratio", 0.0)),
        "column_keep_ratio": float(metrics.get("column_keep_ratio", 0.0)),
    }


def write_diagnostics_csv(rows: list[dict[str, Any]], path: str) -> None:
    """Write schema-linking diagnostics to CSV."""
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    if not rows:
        fieldnames = [
            "question_id",
            "db_id",
            "method_name",
            "question",
            "gold_sql",
            "gold_tables",
            "gold_columns",
            "predicted_tables",
            "predicted_columns",
            "missing_tables",
            "missing_columns",
            "extra_tables",
            "extra_columns",
            "ambiguous_columns",
            "unresolved_columns",
            "table_recall",
            "column_recall",
            "table_precision",
            "column_precision",
            "table_f1",
            "column_f1",
            "table_exact_coverage",
            "column_exact_coverage",
            "table_keep_ratio",
            "column_keep_ratio",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
        return

    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _load_jsonl(path: str) -> list[dict[str, Any]]:
    """
    Load records from:
    - JSONL (one JSON object per line), or
    - JSON array/object files (pretty-printed JSON).
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read().strip()

    if not content:
        return []

    # Fast path: full JSON document (array or object).
    try:
        payload = json.loads(content)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]
    except Exception:
        pass

    # Fallback: strict JSONL.
    items: list[dict[str, Any]] = []
    for i, line in enumerate(content.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception as ex:
            raise ValueError(f"Failed to parse input at line {i}: {ex}") from ex
        if isinstance(obj, dict):
            items.append(obj)
    return items


def _cli_main() -> None:
    parser = argparse.ArgumentParser(description="Standalone schema-linking evaluator")
    parser.add_argument("--input_jsonl", type=str, required=True)
    parser.add_argument("--output_csv", type=str, required=True)
    parser.add_argument("--dialect", type=str, default="sqlite")
    args = parser.parse_args()

    records = _load_jsonl(args.input_jsonl)
    all_results: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []

    for record in records:
        question_id = str(record.get("question_id", ""))
        question = str(record.get("question", ""))
        db_id = str(record.get("db_id", ""))
        schema = record.get("schema", {})
        gold_sql = str(record.get("gold_sql", ""))

        linking_result: dict[str, Any] = {}
        if isinstance(record.get("linking_result"), dict):
            linking_result = record["linking_result"]
        else:
            linking_result = {
                "predicted_tables": _safe_set(record.get("predicted_tables")),
                "predicted_columns": _safe_set(record.get("predicted_columns"), column_like=True),
            }

        evaluated = evaluate_linking_result_against_gold(
            linking_result=linking_result,
            gold_sql=gold_sql,
            schema=schema,
            dialect=args.dialect,
        )
        all_results.append(evaluated)

        gold_required = evaluated["gold_required"]
        for method_name, metrics in evaluated["methods"].items():
            if method_name == "direct":
                predicted = {
                    "tables": _safe_set(linking_result.get("predicted_tables")),
                    "columns": _safe_set(linking_result.get("predicted_columns"), column_like=True),
                }
            else:
                entry = linking_result.get(method_name, {})
                predicted = {
                    "tables": _safe_set(entry.get("tables") if isinstance(entry, dict) else set()),
                    "columns": _safe_set(
                        entry.get("columns") if isinstance(entry, dict) else set(), column_like=True
                    ),
                }
            rows.append(
                build_diagnostic_row(
                    question_id=question_id,
                    question=question,
                    db_id=db_id,
                    gold_sql=gold_sql,
                    method_name=method_name,
                    metrics=metrics,
                    gold_required=gold_required,
                    predicted=predicted,
                )
            )

    write_diagnostics_csv(rows=rows, path=args.output_csv)
    summary = aggregate_schema_linking_metrics(all_results)
    print(json.dumps(summary, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    _cli_main()
