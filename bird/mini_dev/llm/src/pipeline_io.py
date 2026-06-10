from __future__ import annotations

import json
import os
from typing import Any


def new_directory(path: str | None) -> None:
    if path and not os.path.exists(path):
        os.makedirs(path)


def to_json_safe(obj: Any) -> Any:
    if isinstance(obj, set):
        return sorted(to_json_safe(v) for v in obj)
    if isinstance(obj, dict):
        return {str(k): to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [to_json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [to_json_safe(v) for v in obj]
    return obj


def load_eval_data(
    path: str,
    difficulty: str = "all",
    limit: int | None = None,
    dataset: str = "bird",
) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        eval_data = json.load(f)
    eval_data = filter_eval_data_by_difficulty(eval_data, difficulty, dataset)
    if limit is not None:
        eval_data = eval_data[:limit]
    return eval_data


def filter_eval_data_by_difficulty(
    eval_data: list[dict[str, Any]],
    difficulty: str,
    dataset: str = "bird",
) -> list[dict[str, Any]]:
    if not difficulty or difficulty.lower() == "all":
        return eval_data
    if dataset.lower() == "spider":
        return eval_data
    if not any("difficulty" in row for row in eval_data):
        return eval_data
    return [row for row in eval_data if str(row.get("difficulty", "")).lower() == difficulty.lower()]


def decouple_question_schema(datasets: list[dict[str, Any]], db_root_path: str, dataset: str = "bird"):
    question_list = []
    db_path_list = []
    knowledge_list = []
    db_id_list = []
    gold_sql_list = []
    dataset_name = dataset.lower()

    for data in datasets:
        question_list.append(data["question"])
        db_id = data["db_id"]
        if dataset_name == "spider":
            cur_db_path = os.path.join(db_root_path, db_id, "schema.sql")
            knowledge_list.append(None)
            gold_sql_list.append(data.get("query", ""))
        else:
            cur_db_path = os.path.join(db_root_path, db_id, f"{db_id}.sqlite")
            knowledge_list.append(data.get("evidence"))
            gold_sql_list.append(data.get("SQL", ""))
        db_path_list.append(cur_db_path)
        db_id_list.append(db_id)

    return question_list, db_path_list, knowledge_list, db_id_list, gold_sql_list


def build_output_name(
    data_output_path: str,
    mode: str,
    engine: str,
    sql_dialect: str = "PostgreSQL",
) -> str:
    dialect_part = sql_dialect.replace("/", "_").replace(":", "_")
    return (
        data_output_path
        + "predict_"
        + mode
        + "_"
        + engine.replace("/", "_").replace(":", "_")
        + f"_{dialect_part}.json"
    )


def generate_sql_file(sql_lst: list[tuple[str, int]], output_path: str | None = None):
    sql_lst.sort(key=lambda x: x[1])
    result = {}

    for i, (sql, _) in enumerate(sql_lst):
        result[i] = sql

    if output_path:
        directory_path = os.path.dirname(output_path)
        new_directory(directory_path)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=4)

    return result


def generate_debug_file(debug_lst: list[dict[str, Any]], output_path: str | None = None) -> None:
    debug_lst.sort(key=lambda x: x["index"])
    if output_path:
        directory_path = os.path.dirname(output_path)
        new_directory(directory_path)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(debug_lst, f, indent=2)


def generate_linker_eval_jsonl(
    eval_data: list[dict[str, Any]],
    debug_lst: list[dict[str, Any]],
    output_path: str | None = None,
) -> list[dict[str, Any]]:
    """
    Write schema-linking evaluation JSONL rows with gold SQL and combined predictions.
    """
    if not output_path:
        return []

    debug_by_index = {int(item.get("index", -1)): item for item in debug_lst}
    rows = []

    for i, row in enumerate(eval_data):
        debug_row = debug_by_index.get(i, {})
        schema_linking = debug_row.get("schema_linking") or {}
        combined = schema_linking.get("combined_schema") or {}
        schema_for_eval = schema_linking.get("schema_passed_to_linker")
        gold_sql = row.get("SQL") or row.get("query") or row.get("sql") or ""
        if not isinstance(gold_sql, str):
            gold_sql = ""

        if not schema_for_eval:
            continue

        rows.append(
            {
                "question_id": row.get("question_id", i),
                "question": row.get("question", ""),
                "db_id": row.get("db_id", ""),
                "gold_sql": gold_sql,
                "schema": schema_for_eval,
                "combined_schema": {
                    "predicted_tables": combined.get("predicted_tables", []),
                    "predicted_columns": combined.get("predicted_columns", []),
                },
                "predicted_tables": combined.get("predicted_tables", []),
                "predicted_columns": combined.get("predicted_columns", []),
            }
        )

    directory_path = os.path.dirname(output_path)
    new_directory(directory_path)
    with open(output_path, "w", encoding="utf-8") as f:
        for item in rows:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    return rows


def post_process_response(sql: str, db_path: str, dataset: str = "bird", db_id: str | None = None) -> str:
    if not db_id:
        normalized = os.path.normpath(db_path)
        base = os.path.basename(normalized)
        if base == "schema.sql":
            db_id = os.path.basename(os.path.dirname(normalized))
        else:
            db_id = os.path.splitext(base)[0]
    return f"{sql}\t----- {dataset.lower()} -----\t{db_id}"
