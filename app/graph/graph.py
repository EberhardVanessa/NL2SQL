from __future__ import annotations

from langgraph.types import Command

from app.graph.build_graph import build_graph
from app.file.storage import FileRegistry
from app.graph.sql_validation import normalize_sql_dialect

graph = build_graph()

STEP_LABELS = {
    "check_thinking": "Selecting graph path",
    "check_non_thinking_interaction": "Checking interaction mode",
    "decide": "Checking whether clarification is needed",
    "hitl": "Waiting for clarification",
    "generate_answer": "Generating answer",
    "get_schema": "Extracting relevant schema",
    "check_schema_clarification": "Checking schema ambiguity",
    "get_tables_columns": "Identifying tables and columns",
    "check_tables_columns_clarification": "Checking table and column ambiguity",
    "get_where_conditions": "Identifying filters and conditions",
    "check_where_conditions_clarification": "Checking filter ambiguity",
    "get_limit_aggregation": "Identifying limits and aggregations",
    "check_limit_aggregation_clarification": "Checking aggregation ambiguity",
    "create_query": "Creating SQL query",
    "check_query_clarification": "Checking generated query ambiguity",
    "verify_query": "Verifying SQL query",
    "validate_query": "Validating SQL query",
    "repair_query": "Repairing SQL query",
    "thinking_hitl": "Waiting for clarification",
    "generate_query_answer": "Preparing final SQL",
}


def extract_schema_context(file_registry: FileRegistry, schema_name: str) -> str:
    schema_context = file_registry.get_schema_content(schema_name)
    if schema_context is None:
        schema_context = file_registry.combined_text().strip()
    return schema_context


def emit_graph_step(node_name: str, emit_status=None) -> None:
    if not emit_status or node_name == "__start__":
        return

    label = STEP_LABELS.get(node_name, node_name.replace("_", " ").title())
    emit_status(
        {
            "type": "status",
            "description": f"Current graph step: {label}",
            "node": node_name,
        }
    )


def extract_interrupt_result(config: dict) -> dict | None:
    state = graph.get_state(config)
    interrupt_value = None

    if state.tasks:
        for task in state.tasks:
            if getattr(task, "interrupts", None):
                for intr in task.interrupts:
                    interrupt_value = intr.value
                    break

    if interrupt_value and isinstance(interrupt_value, dict):
        return {
            "status": "needs_human",
            "follow_up_question": interrupt_value.get("question", "Please clarify."),
            "reached_end": False,
        }

    return None


def result_from_state(config: dict, result: dict | None = None) -> dict:
    state_values = result or graph.get_state(config).values

    if not state_values.get("answer"):
        interrupt_result = extract_interrupt_result(config)
        if interrupt_result:
            return interrupt_result

        return {
            "status": "needs_human",
            "follow_up_question": "Please clarify your request.",
            "reached_end": False,
        }

    return {
        "status": "ok",
        "answer": state_values["answer"],
        "sql_validation": state_values.get("sql_validation"),
        "sql_repair_attempts": state_values.get("sql_repair_attempts", []),
        "reached_end": True,
    }


def result_from_debug_interrupt(interrupts: list | tuple) -> dict | None:
    if not interrupts:
        return None

    first_interrupt = interrupts[0]
    interrupt_value = None

    if isinstance(first_interrupt, dict):
        interrupt_value = first_interrupt.get("value")
    else:
        interrupt_value = getattr(first_interrupt, "value", None)

    if isinstance(interrupt_value, dict):
        return {
            "status": "needs_human",
            "follow_up_question": interrupt_value.get("question", "Please clarify."),
            "reached_end": False,
        }

    return {
        "status": "needs_human",
        "follow_up_question": "Please clarify your request.",
        "reached_end": False,
    }


def stream_graph_debug(input_value, config: dict, emit_status=None) -> dict | None:
    for event in graph.stream(input_value, config=config, stream_mode="debug"):
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        payload = event.get("payload") or {}

        if event_type == "task":
            emit_graph_step(payload.get("name", ""), emit_status)
            continue

        if event_type == "task_result":
            interrupt_result = result_from_debug_interrupt(
                payload.get("interrupts") or []
            )
            if interrupt_result:
                return interrupt_result

    return None

def ask_question(
    thread_id: str,
    question: str,
    file_registry: FileRegistry,
    is_thinking: bool,
    skip_user_interaction: bool,
    schema_name: str,
    schema_context_base: str,
    sql_dialect: str = "sqlite",
    max_sql_repair_attempts: int = 2,
    emit_status=None,
) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    if schema_context_base is not None:
        schema_context = schema_context_base
    else:
        schema_context = extract_schema_context(file_registry, schema_name)

    result = graph.invoke(
        {
            "question": question,
            "schema_context": schema_context,
            "thinking_enabled": is_thinking,
            "skip_user_interaction": skip_user_interaction,
            "sql_dialect": normalize_sql_dialect(sql_dialect),
            "max_sql_repair_attempts": max(0, min(int(max_sql_repair_attempts), 5)),
        },
        config=config,
    )

    return result_from_state(config, result)


def stream_question(
    thread_id: str,
    question: str,
    file_registry: FileRegistry,
    is_thinking: bool,
    skip_user_interaction: bool,
    schema_name: str,
    schema_context_base: str,
    sql_dialect: str = "sqlite",
    max_sql_repair_attempts: int = 2,
    emit_status=None,
) -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    if schema_context_base is not None:
        schema_context = schema_context_base
    else:
        schema_context = extract_schema_context(file_registry, schema_name)

    interrupt_result = stream_graph_debug(
        {
            "question": question,
            "schema_context": schema_context,
            "thinking_enabled": is_thinking,
            "skip_user_interaction": skip_user_interaction,
            "sql_dialect": normalize_sql_dialect(sql_dialect),
            "max_sql_repair_attempts": max(0, min(int(max_sql_repair_attempts), 5)),
        },
        config=config,
        emit_status=emit_status,
    )
    if interrupt_result:
        return interrupt_result

    return result_from_state(config)


def resume_question(thread_id: str, human_answer: str) -> dict:
    config = {"configurable": {"thread_id": thread_id}}

    result = graph.invoke(
        Command(resume=human_answer),
        config=config,
    )

    return result_from_state(config, result)


def stream_resume_question(
    thread_id: str,
    human_answer: str,
    emit_status=None,
) -> dict:
    config = {"configurable": {"thread_id": thread_id}}

    interrupt_result = stream_graph_debug(
        Command(resume=human_answer),
        config=config,
        emit_status=emit_status,
    )
    if interrupt_result:
        return interrupt_result

    return result_from_state(config)
