from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from llm_client import LLMGateway, SchemaLinkerLLMClient
from schema_linker import (
    filter_schema,
    get_linked_schema_for_question,
    serialize_schema,
    serialize_schema_all,
)
from table_schema import generate_schema_dict


@dataclass(frozen=True)
class SchemaLinkingOptions:
    mode: str = "none"
    combine_strategy: str = "union"
    only: bool = False
    representations: tuple[str, ...] = ("ddl", "m_schema", "mac_schema")

    @property
    def enabled(self) -> bool:
        return self.only or self.mode.lower() != "none"


def run_schema_linking_for_question(
    llm: LLMGateway,
    db_path: str,
    question: str,
    knowledge: str | None,
    options: SchemaLinkingOptions,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    initial_schema = generate_schema_dict(db_path)
    schema_for_linker = copy.deepcopy(initial_schema)
    schema_variants = serialize_schema_all(schema_for_linker)

    linker_question = question
    if knowledge:
        linker_question = f"{question}\n\nExternal Knowledge:\n{knowledge}"

    linked = get_linked_schema_for_question(
        question=linker_question,
        schema=schema_for_linker,
        client=SchemaLinkerLLMClient(llm, temperature=0.0, max_tokens=768),
        mode=options.mode,
        combine_strategy=options.combine_strategy,
        representations=list(options.representations),
    )

    linked_by_representation = {}
    linked_filtered_schemas = {}
    for rep in ["ddl", "m_schema", "mac_schema"]:
        rep_data = linked.get("linking_result", {}).get(rep, {})
        rep_tables = set(rep_data.get("tables", set()))
        rep_columns = set(rep_data.get("columns", set()))
        linked_by_representation[rep] = {
            "predicted_tables": sorted(list(rep_tables)),
            "predicted_columns": sorted(list(rep_columns)),
            "error": rep_data.get("error"),
            "raw_response": rep_data.get("raw_response"),
        }
        linked_filtered_schemas[rep] = filter_schema(
            schema=copy.deepcopy(schema_for_linker),
            predicted_tables=rep_tables,
            predicted_columns=rep_columns,
            mode=options.mode,
        )

    combined_name = options.combine_strategy.lower()
    combined = linked.get("linking_result", {}).get(combined_name, {})
    combined_schema = {
        "predicted_tables": sorted(list(combined.get("tables", set()))),
        "predicted_columns": sorted(list(combined.get("columns", set()))),
    }

    schema_prompt_for_sql = serialize_schema(linked["filtered_schema"], "ddl")

    debug_payload = {
        "initial_schema": initial_schema,
        "schema_passed_to_linker": schema_for_linker,
        "created_schemas": schema_variants,
        "linked_schemas": linked_by_representation,
        "linked_filtered_schemas": linked_filtered_schemas,
        "combined_schema": combined_schema,
        "filtered_schema": linked.get("filtered_schema"),
        "mode": options.mode,
        "combine_strategy": options.combine_strategy,
    }

    return linked, schema_prompt_for_sql, debug_payload
