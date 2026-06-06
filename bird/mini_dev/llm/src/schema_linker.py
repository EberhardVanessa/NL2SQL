from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from typing import Any, Optional, Protocol


SUPPORTED_REPRESENTATIONS = ("ddl", "m_schema", "mac_schema")


class SchemaLinkerClient(Protocol):
    """Protocol for provider-agnostic LLM completion clients."""

    def complete(self, prompt: str) -> str:
        """Return raw model text for the prompt."""
        ...


@dataclass(frozen=True)
class SchemaIndex:
    """Normalized schema index used for linking and filtering."""

    tables: dict[str, dict[str, Any]]
    table_columns: dict[str, set[str]]
    foreign_keys: list[dict[str, str]]


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
    table = parts[-2]
    column = parts[-1]
    if not table or not column:
        return ""
    return f"{table}.{column}"


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _extract_foreign_keys(schema: dict[str, Any], table_columns: dict[str, set[str]]) -> list[dict[str, str]]:
    """Extract foreign keys from top-level and per-column schema variants."""
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add_fk(src_table: Any, src_col: Any, dst_table: Any, dst_col: Any) -> None:
        st = _norm_identifier(src_table)
        sc = _norm_identifier(src_col)
        dt = _norm_identifier(dst_table)
        dc = _norm_identifier(dst_col)
        if not st or not sc or not dt or not dc:
            return
        key = (st, sc, dt, dc)
        if key in seen:
            return
        seen.add(key)
        normalized.append(
            {
                "source_table": st,
                "source_column": sc,
                "target_table": dt,
                "target_column": dc,
            }
        )

    for fk in _as_list(schema.get("foreign_keys")):
        if not isinstance(fk, dict):
            continue
        src_table = fk.get("source_table") or fk.get("table") or fk.get("from_table") or fk.get("table_name")
        src_col = fk.get("source_column") or fk.get("column") or fk.get("from_column") or fk.get("column_name")
        dst_table = (
            fk.get("target_table")
            or fk.get("references_table")
            or fk.get("to_table")
            or fk.get("foreign_table_name")
            or fk.get("referred_table")
        )
        dst_col = (
            fk.get("target_column")
            or fk.get("references_column")
            or fk.get("to_column")
            or fk.get("foreign_column_name")
            or fk.get("referred_column")
        )
        if isinstance(fk.get("from"), dict):
            src_table = fk["from"].get("table", src_table)
            src_col = fk["from"].get("column", src_col)
        if isinstance(fk.get("to"), dict):
            dst_table = fk["to"].get("table", dst_table)
            dst_col = fk["to"].get("column", dst_col)
        add_fk(src_table, src_col, dst_table, dst_col)

    tables = schema.get("tables", {}) if isinstance(schema.get("tables"), dict) else {}
    for table_name, table_data in tables.items():
        t = _norm_identifier(table_name)
        cols = table_data.get("columns", {}) if isinstance(table_data, dict) else {}
        if not isinstance(cols, dict):
            continue
        for col_name, col_data in cols.items():
            c = _norm_identifier(col_name)
            if not c:
                continue
            fk = col_data.get("foreign_key") if isinstance(col_data, dict) else None
            if isinstance(fk, str):
                parsed = _norm_column(fk)
                if parsed:
                    dt, dc = parsed.split(".", 1)
                    add_fk(t, c, dt, dc)
            elif isinstance(fk, dict):
                dst_table = (
                    fk.get("table")
                    or fk.get("target_table")
                    or fk.get("references_table")
                    or fk.get("foreign_table_name")
                )
                dst_col = (
                    fk.get("column")
                    or fk.get("target_column")
                    or fk.get("references_column")
                    or fk.get("foreign_column_name")
                )
                if not dst_table and isinstance(fk.get("to"), dict):
                    dst_table = fk["to"].get("table")
                    dst_col = fk["to"].get("column", dst_col)
                add_fk(t, c, dst_table, dst_col)

    # Remove FK entries that point to unknown schema elements.
    filtered: list[dict[str, str]] = []
    for fk in normalized:
        st = fk["source_table"]
        sc = fk["source_column"]
        dt = fk["target_table"]
        dc = fk["target_column"]
        if st in table_columns and dt in table_columns and sc in table_columns[st] and dc in table_columns[dt]:
            filtered.append(fk)
    return filtered


def _build_schema_index(schema: dict[str, Any]) -> SchemaIndex:
    tables_raw = schema.get("tables", {}) if isinstance(schema.get("tables"), dict) else {}
    tables: dict[str, dict[str, Any]] = {}
    table_columns: dict[str, set[str]] = {}

    for raw_table_name, raw_table_data in tables_raw.items():
        table_name = _norm_identifier(raw_table_name)
        if not table_name or not isinstance(raw_table_data, dict):
            continue
        raw_columns = raw_table_data.get("columns", {})
        if not isinstance(raw_columns, dict):
            raw_columns = {}
        normalized_columns: dict[str, dict[str, Any]] = {}
        for raw_col_name, raw_col_data in raw_columns.items():
            col_name = _norm_identifier(raw_col_name)
            if not col_name:
                continue
            normalized_columns[col_name] = raw_col_data if isinstance(raw_col_data, dict) else {}
        table_data = dict(raw_table_data)
        table_data["columns"] = normalized_columns
        tables[table_name] = table_data
        table_columns[table_name] = set(normalized_columns.keys())

    foreign_keys = _extract_foreign_keys(schema, table_columns)
    return SchemaIndex(tables=tables, table_columns=table_columns, foreign_keys=foreign_keys)


def _schema_relationship_lines(index: SchemaIndex) -> list[str]:
    lines: list[str] = []
    for fk in index.foreign_keys:
        lines.append(
            f"{fk['source_table']}.{fk['source_column']} -> {fk['target_table']}.{fk['target_column']}"
        )
    return lines


def serialize_schema(schema: dict[str, Any], representation: str) -> str:
    """Serialize schema into one of: ddl, m_schema, mac_schema."""
    rep = _norm_identifier(representation)
    if rep not in SUPPORTED_REPRESENTATIONS:
        raise ValueError(f"Unsupported representation: {representation}")

    index = _build_schema_index(schema)

    if rep == "ddl":
        return _serialize_schema_ddl(index)
    if rep == "m_schema":
        return _serialize_schema_m_schema(index)
    return _serialize_schema_mac_schema(index)


def _serialize_schema_ddl(index: SchemaIndex) -> str:
    blocks: list[str] = []
    fk_map: dict[str, list[dict[str, str]]] = {}
    for fk in index.foreign_keys:
        fk_map.setdefault(fk["source_table"], []).append(fk)

    for table_name in sorted(index.tables):
        table_data = index.tables[table_name]
        columns = table_data.get("columns", {})
        lines: list[str] = []
        for col_name in sorted(columns):
            col_data = columns[col_name]
            col_type = str(col_data.get("type", "TEXT")).upper()
            suffixes: list[str] = []
            if bool(col_data.get("primary_key")):
                suffixes.append("PRIMARY KEY")
            line = f"    {col_name} {col_type}"
            if suffixes:
                line += " " + " ".join(suffixes)
            lines.append(line)
        for fk in fk_map.get(table_name, []):
            lines.append(
                f"    FOREIGN KEY ({fk['source_column']}) REFERENCES {fk['target_table']}({fk['target_column']})"
            )
        inside = ",\n".join(lines)
        blocks.append(f"CREATE TABLE {table_name} (\n{inside}\n);")
    return "\n\n".join(blocks)


def _serialize_schema_m_schema(index: SchemaIndex) -> str:
    lines: list[str] = ["Database Schema:"]
    for table_name in sorted(index.tables):
        table_data = index.tables[table_name]
        lines.append(f"Table: {table_name}")
        table_desc = table_data.get("description")
        if table_desc:
            lines.append(f"Description: {table_desc}")
        lines.append("Columns:")
        columns = table_data.get("columns", {})
        for col_name in sorted(columns):
            col_data = columns[col_name]
            lines.append(f"- {col_name}")
            if col_data.get("type") is not None:
                lines.append(f"  Type: {col_data.get('type')}")
            if "primary_key" in col_data:
                lines.append(f"  Primary Key: {'yes' if bool(col_data.get('primary_key')) else 'no'}")
            if col_data.get("foreign_key"):
                lines.append(f"  Foreign Key: {col_data.get('foreign_key')}")
            if col_data.get("description"):
                lines.append(f"  Description: {col_data.get('description')}")
            examples = col_data.get("examples")
            if isinstance(examples, list) and examples:
                lines.append("  Examples: " + ", ".join(str(x) for x in examples))
        lines.append("")
    rel_lines = _schema_relationship_lines(index)
    if rel_lines:
        lines.append("Relationships:")
        for rel in rel_lines:
            lines.append(f"- {rel}")
    return "\n".join(lines).strip()


def _serialize_schema_mac_schema(index: SchemaIndex) -> str:
    lines: list[str] = []
    for table_name in sorted(index.tables):
        table_data = index.tables[table_name]
        table_desc = str(table_data.get("description", "")).strip()
        if table_desc:
            lines.append(f"[{table_name}] {table_desc}")
        else:
            lines.append(f"[{table_name}]")
        columns = table_data.get("columns", {})
        for col_name in sorted(columns):
            col_data = columns[col_name]
            parts: list[str] = [f"{col_name}: {str(col_data.get('type', 'TEXT')).upper()}"]
            if bool(col_data.get("primary_key")):
                parts.append("PK")
            if col_data.get("description"):
                parts.append(f"desc: {col_data.get('description')}")
            examples = col_data.get("examples")
            if isinstance(examples, list) and examples:
                parts.append("examples: " + ", ".join(str(x) for x in examples))
            lines.append("  " + " | ".join(parts))
    rel_lines = _schema_relationship_lines(index)
    if rel_lines:
        lines.append("Relationships: " + "; ".join(rel_lines))
    return "\n".join(lines).strip()


def serialize_schema_all(schema: dict[str, Any]) -> dict[str, str]:
    """Return all supported schema serializations."""
    return {name: serialize_schema(schema, name) for name in SUPPORTED_REPRESENTATIONS}


def build_schema_linking_prompt(
    question: str,
    schema_text: str,
    representation: str,
    few_shot_examples: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Build strict JSON-output prompt for schema linking."""
    rep = _norm_identifier(representation)
    if rep not in SUPPORTED_REPRESENTATIONS:
        raise ValueError(f"Unsupported representation: {representation}")

    prompt_parts: list[str] = [
        "You are a schema-linking assistant for text-to-SQL.",
        f"Schema Representation: {rep}",
        "",
        "Task:",
        "Identify ALL tables and columns required to answer the natural-language question.",
        "",
        "Output format (strict JSON only):",
        '{',
        '  "tables": ["table_name"],',
        '  "columns": ["table_name.column_name"]',
        '}',
        "",
        "Rules:",
        "1) Return only valid JSON. No markdown. No explanation.",
        "2) Use only table names and columns that exist in the schema.",
        '3) Use lowercase identifiers.',
        '4) Use table-qualified columns only: "table.column".',
        "5) Include every table and column needed for SELECT, FROM, JOIN/ON, WHERE, GROUP BY, HAVING, ORDER BY, aggregations, subqueries, CTEs, and set operations.",
        "6) Prioritize recall over precision.",
        "7) Extra tables/columns are less harmful than missing required ones.",
        "8) Do not invent schema items.",
        "9) If unsure, include potentially relevant table/column.",
        "",
    ]

    examples = few_shot_examples or []
    if examples:
        prompt_parts.append("Few-shot examples:")
        for i, ex in enumerate(examples, start=1):
            ex_q = str(ex.get("question", "")).strip()
            ex_schema = ex.get("schema_text")
            ex_answer = ex.get("answer", {})
            tables = ex_answer.get("tables", []) if isinstance(ex_answer, dict) else []
            columns = ex_answer.get("columns", []) if isinstance(ex_answer, dict) else []
            prompt_parts.append(f"Example {i}:")
            prompt_parts.append(f"Question: {ex_q}")
            if ex_schema:
                prompt_parts.append("Schema:")
                prompt_parts.append(str(ex_schema))
            prompt_parts.append(
                "Answer JSON: "
                + json.dumps(
                    {
                        "tables": [_norm_identifier(t) for t in _as_list(tables)],
                        "columns": [_norm_column(c) for c in _as_list(columns) if _norm_column(c)],
                    },
                    ensure_ascii=True,
                )
            )
            prompt_parts.append("")

    prompt_parts.extend(
        [
            "Schema:",
            schema_text,
            "",
            "Question:",
            question,
            "",
            "Return JSON now.",
        ]
    )

    return "\n".join(prompt_parts).strip()


def parse_linker_response(raw_response: str) -> dict[str, Any]:
    """Parse strict/near-strict JSON response into normalized sets."""
    text = raw_response.strip() if isinstance(raw_response, str) else ""
    payload: Any = None
    error: Optional[str] = None

    try:
        payload = json.loads(text)
    except Exception as ex:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(0))
            except Exception as ex2:
                error = f"json_parse_error: {ex2}"
        else:
            error = f"json_parse_error: {ex}"

    tables: set[str] = set()
    columns: set[str] = set()

    if isinstance(payload, dict):
        for item in _as_list(payload.get("tables")):
            t = _norm_identifier(item)
            if t:
                tables.add(t)
        for item in _as_list(payload.get("columns")):
            c = _norm_column(item)
            if c:
                columns.add(c)
                tables.add(c.split(".", 1)[0])
    else:
        if error is None:
            error = "parsed_payload_not_dict"

    return {
        "tables": tables,
        "columns": columns,
        "error": error,
    }


def run_schema_linking(
    question: str,
    schema: dict[str, Any],
    client: SchemaLinkerClient,
    representations: list[str] = ["ddl", "m_schema", "mac_schema"],
    few_shot_examples: Optional[list[dict[str, Any]]] = None,
) -> dict[str, dict[str, Any]]:
    """Run LLM schema linking per schema representation."""
    schema_variants = serialize_schema_all(schema)
    results: dict[str, dict[str, Any]] = {}

    for rep in representations:
        normalized_rep = _norm_identifier(rep)
        if normalized_rep not in SUPPORTED_REPRESENTATIONS:
            results[rep] = {
                "tables": set(),
                "columns": set(),
                "error": f"unsupported_representation: {rep}",
                "raw_response": "",
                "prompt": "",
            }
            continue

        schema_text = schema_variants[normalized_rep]
        prompt = build_schema_linking_prompt(
            question=question,
            schema_text=schema_text,
            representation=normalized_rep,
            few_shot_examples=few_shot_examples,
        )
        raw_response = client.complete(prompt)
        parsed = parse_linker_response(raw_response)
        results[normalized_rep] = {
            "tables": set(parsed["tables"]),
            "columns": set(parsed["columns"]),
            "error": parsed.get("error"),
            "raw_response": raw_response,
            "prompt": prompt,
            "representation": normalized_rep,
        }
    return results


def combine_linking_outputs(linking_result: dict[str, Any], strategy: str = "union") -> dict[str, set[str]]:
    """Combine per-representation outputs via union or intersection."""
    method = _norm_identifier(strategy)
    if method not in {"union", "intersection"}:
        raise ValueError(f"Unsupported combine strategy: {strategy}")

    valid_entries: list[dict[str, Any]] = []
    for rep in SUPPORTED_REPRESENTATIONS:
        data = linking_result.get(rep)
        if isinstance(data, dict):
            valid_entries.append(data)

    if not valid_entries:
        return {"tables": set(), "columns": set()}

    if method == "union":
        tables = set()
        columns = set()
        for item in valid_entries:
            tables |= set(item.get("tables", set()))
            columns |= set(item.get("columns", set()))
        return {"tables": tables, "columns": columns}

    tables = None
    columns = None
    for item in valid_entries:
        t = set(item.get("tables", set()))
        c = set(item.get("columns", set()))
        tables = t if tables is None else tables & t
        columns = c if columns is None else columns & c
    return {"tables": tables or set(), "columns": columns or set()}


def _filter_foreign_keys(
    foreign_keys: list[dict[str, Any]],
    kept_tables: set[str],
    kept_columns: Optional[dict[str, set[str]]] = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for fk in foreign_keys:
        st = _norm_identifier(fk.get("source_table"))
        sc = _norm_identifier(fk.get("source_column"))
        dt = _norm_identifier(fk.get("target_table"))
        dc = _norm_identifier(fk.get("target_column"))
        if st not in kept_tables or dt not in kept_tables:
            continue
        if kept_columns is not None:
            if sc and sc not in kept_columns.get(st, set()):
                continue
            if dc and dc not in kept_columns.get(dt, set()):
                continue
        out.append(copy.deepcopy(fk))
    return out


def filter_schema(
    schema: dict[str, Any],
    predicted_tables: set[str],
    predicted_columns: set[str],
    mode: str,
) -> dict[str, Any]:
    """
    Filter schema by mode.

    Full-mode behavior:
    If a table is predicted but no columns are predicted for that table, keep all
    columns from that table to preserve recall.
    """
    normalized_mode = _norm_identifier(mode)
    if normalized_mode not in {"none", "table_only", "full"}:
        raise ValueError(f"Unsupported mode: {mode}")

    if normalized_mode == "none":
        return copy.deepcopy(schema)

    original = copy.deepcopy(schema)
    index = _build_schema_index(original)
    predicted_tables = {_norm_identifier(t) for t in predicted_tables if _norm_identifier(t)}
    normalized_predicted_columns = {_norm_column(c) for c in predicted_columns if _norm_column(c)}

    tables_out: dict[str, Any] = {}
    table_columns_out: dict[str, set[str]] = {}

    if normalized_mode == "table_only":
        kept_tables = {t for t in predicted_tables if t in index.tables}
        for table in kept_tables:
            table_data = copy.deepcopy(index.tables[table])
            tables_out[table] = table_data
            cols = table_data.get("columns", {})
            table_columns_out[table] = set(cols.keys()) if isinstance(cols, dict) else set()

        original["tables"] = {t: tables_out[t] for t in sorted(tables_out)}
        original["foreign_keys"] = _filter_foreign_keys(index.foreign_keys, kept_tables)
        return original

    columns_by_table: dict[str, set[str]] = {}
    for item in normalized_predicted_columns:
        table, column = item.split(".", 1)
        columns_by_table.setdefault(table, set()).add(column)

    kept_tables = {t for t in predicted_tables if t in index.tables}
    kept_tables |= {t for t in columns_by_table if t in index.tables}

    for table in kept_tables:
        source_table = index.tables[table]
        source_cols = source_table.get("columns", {})
        if not isinstance(source_cols, dict):
            source_cols = {}
        requested_cols = columns_by_table.get(table, set())

        # Recall-oriented behavior: keep all columns if table predicted but no columns predicted for it.
        if table in predicted_tables and not requested_cols:
            kept_cols = set(source_cols.keys())
        else:
            kept_cols = {c for c in requested_cols if c in source_cols}

        new_table = copy.deepcopy(source_table)
        new_table["columns"] = {c: copy.deepcopy(source_cols[c]) for c in sorted(kept_cols)}
        tables_out[table] = new_table
        table_columns_out[table] = kept_cols

    original["tables"] = {t: tables_out[t] for t in sorted(tables_out)}
    original["foreign_keys"] = _filter_foreign_keys(index.foreign_keys, kept_tables, table_columns_out)
    return original


def link_and_filter_schema(
    question: str,
    schema: dict[str, Any],
    client: SchemaLinkerClient,
    mode: str = "table_only",
    representations: list[str] = ["ddl", "m_schema", "mac_schema"],
    few_shot_examples: Optional[list[dict[str, Any]]] = None,
    combine_strategy: str = "union",
) -> dict[str, Any]:
    """Run linking, combine multi-representation outputs, and filter schema."""
    per_rep = run_schema_linking(
        question=question,
        schema=schema,
        client=client,
        representations=representations,
        few_shot_examples=few_shot_examples,
    )

    union_result = combine_linking_outputs(per_rep, strategy="union")
    intersection_result = combine_linking_outputs(per_rep, strategy="intersection")
    combined = combine_linking_outputs(per_rep, strategy=combine_strategy)

    filtered = filter_schema(
        schema=schema,
        predicted_tables=combined["tables"],
        predicted_columns=combined["columns"],
        mode=mode,
    )

    linking_result: dict[str, Any] = dict(per_rep)
    linking_result["union"] = {"tables": set(union_result["tables"]), "columns": set(union_result["columns"])}
    linking_result["intersection"] = {
        "tables": set(intersection_result["tables"]),
        "columns": set(intersection_result["columns"]),
    }

    return {
        "mode": _norm_identifier(mode),
        "combine_strategy": _norm_identifier(combine_strategy),
        "filtered_schema": filtered,
        "predicted_tables": set(combined["tables"]),
        "predicted_columns": set(combined["columns"]),
        "linking_result": linking_result,
    }


def get_linked_schema_for_question(
    question: str,
    schema: dict[str, Any],
    client: SchemaLinkerClient,
    mode: str = "table_only",
    combine_strategy: str = "union",
    representations: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Integration helper for existing text-to-SQL pipeline calls."""
    return link_and_filter_schema(
        question=question,
        schema=schema,
        client=client,
        mode=mode,
        combine_strategy=combine_strategy,
        representations=representations or ["ddl", "m_schema", "mac_schema"],
    )


class _DemoClient:
    """Small demo-only fake client."""

    def complete(self, prompt: str) -> str:
        _ = prompt
        return json.dumps(
            {
                "tables": ["singer", "concert"],
                "columns": ["singer.singer_id", "singer.name", "concert.singer_id"],
            }
        )


if __name__ == "__main__":
    toy_schema = {
        "tables": {
            "singer": {
                "description": "Singer information",
                "columns": {
                    "singer_id": {"type": "INTEGER", "primary_key": True},
                    "name": {"type": "TEXT"},
                    "age": {"type": "INTEGER"},
                },
            },
            "concert": {
                "columns": {
                    "concert_id": {"type": "INTEGER", "primary_key": True},
                    "singer_id": {"type": "INTEGER", "foreign_key": "singer.singer_id"},
                    "venue": {"type": "TEXT"},
                }
            },
        },
        "foreign_keys": [
            {
                "source_table": "concert",
                "source_column": "singer_id",
                "target_table": "singer",
                "target_column": "singer_id",
            }
        ],
    }
    demo = get_linked_schema_for_question(
        question="List singer names that have concerts.",
        schema=toy_schema,
        client=_DemoClient(),
        mode="full",
        combine_strategy="union",
    )
    print(json.dumps({"mode": demo["mode"], "tables": sorted(demo["predicted_tables"])}, indent=2))
