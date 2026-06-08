from app.graph.agent_state import AgentState
from langgraph.types import interrupt

import json
import logging
import re
from typing import Any

from app.graph.sql_validation import (
    display_sql_dialect,
    validate_sql,
    validation_error_message,
)
from app.graph.thinking import thinking_prompts
from app.llm.utils import build_llm

logger = logging.getLogger(__name__)
MAX_CHECK_LOG_CHARS = 4000

def route_after_step_for_optional_check(state: AgentState) -> str:
    if state.get("skip_user_interaction", False):
        return "skip_check"

    return "check"

def route_after_thinking_check(state: AgentState) -> str:
    if state.get("thinking_enabled", False):
        return "thinking"

    return "no_thinking"

def thinking_hitl_node(state: AgentState) -> AgentState:
    question = state.get(
        "thinking_follow_up_question",
        "Please clarify your request.",
    )
    return_to = state.get("thinking_return_to", "")

    answer = interrupt(
        {
            "type": "thinking_clarification_request",
            "question": question,
            "return_to": return_to,
        }
    )
    answer_text = str(answer)
    history = list(state.get("thinking_clarification_history", []))
    history.append(
        {
            "step": return_to,
            "question": question,
            "answer": answer_text,
        }
    )

    return {
        "thinking_clarification_answer": answer_text,
        "thinking_clarification_history": history,
        "thinking_needs_clarification": False,
        "thinking_follow_up_question": "",
    }

def route_after_thinking_clarification_check(state: AgentState) -> str:
    if state.get("skip_user_interaction", False):
        return "continue"

    if state.get("thinking_needs_clarification", False):
        return "thinking_hitl"

    return "continue"

def build_clarification_block(state: AgentState) -> str:
    history = state.get("thinking_clarification_history", [])

    if not history:
        return "\n"

    lines = ["", "Additional user clarifications:"]
    for item in history:
        step = item.get("step", "unknown_step")
        question = item.get("question", "").strip()
        answer = item.get("answer", "").strip()
        if question:
            lines.append(f"- At {step}, asked: {question}")
        lines.append(f"  User answered: {answer}")
    lines.append("")
    return "\n".join(lines)

def strip_json_markdown_fence(content: str) -> str:
    text = (content or "").strip()
    match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()

    return text

def parse_json_value(content: str) -> Any:
    text = strip_json_markdown_fence(content)

    try:
        return json.loads(text)
    except Exception:
        pass

    object_start = text.find("{")
    object_end = text.rfind("}")
    if object_start != -1 and object_end > object_start:
        return json.loads(text[object_start : object_end + 1])

    array_start = text.find("[")
    array_end = text.rfind("]")
    if array_start != -1 and array_end > array_start:
        return json.loads(text[array_start : array_end + 1])

    raise json.JSONDecodeError("No JSON object or array found", text, 0)

def parse_json_artifact(content: str, artifact_name: str) -> dict[str, Any]:
    try:
        data = parse_json_value(content)
    except Exception as exc:
        return {
            "parse_error": str(exc),
            "raw": content,
        }

    if isinstance(data, dict):
        return data

    return {
        artifact_name: data,
    }

def format_artifact(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)

    return str(value)

def truncate_for_log(value: Any, max_chars: int = MAX_CHECK_LOG_CHARS) -> str:
    text = format_artifact(value)
    if len(text) <= max_chars:
        return text

    return f"{text[:max_chars]}... [truncated {len(text) - max_chars} chars]"

def fallback_follow_up_question(content: str) -> str:
    raw = " ".join((content or "").strip().split())
    if not raw:
        return "Please clarify your request before I generate SQL."

    question_match = re.search(r"([^.!?]*\?)", raw)
    if question_match:
        question = question_match.group(1).strip().strip('"')
        return f"Please clarify: {question}"

    before_reason = re.split(r'"\s*,\s*"reason"\s*:', raw, maxsplit=1)[0]
    before_reason = before_reason.strip().strip('`"{}, ')
    if before_reason:
        return f"Please clarify: {before_reason}"

    return "Please clarify your request before I generate SQL."

def parse_clarification_response(content: str, check_name: str) -> dict[str, Any]:
    try:
        data = parse_json_value(content)
    except Exception as exc:
        logger.warning(
            "HITL check %s returned invalid JSON: %s | failing safe to clarification | raw_response=%s",
            check_name,
            exc,
            truncate_for_log(content),
        )
        return {
            "needs_clarification": True,
            "follow_up_question": fallback_follow_up_question(content),
            "reason": "Clarification check returned invalid JSON.",
            "parse_error": str(exc),
        }

    if not isinstance(data, dict):
        logger.warning(
            "HITL check %s returned non-object JSON; failing safe to clarification: %s",
            check_name,
            truncate_for_log(data),
        )
        return {
            "needs_clarification": True,
            "follow_up_question": "Please clarify your request before I generate SQL.",
            "reason": "Clarification check returned non-object JSON.",
            "parse_error": "response was not a JSON object",
        }

    if data.get("needs_clarification") and not str(data.get("follow_up_question", "")).strip():
        data["follow_up_question"] = "Please clarify your request before I generate SQL."

    return data

def log_clarification_check(
    *,
    check_name: str,
    state: AgentState,
    checked_artifact_name: str,
    checked_artifact: Any,
    raw_response: str,
    parsed_response: dict[str, Any],
) -> None:
    logger.info(
        (
            "HITL check %s | question=%r | artifact_name=%s | "
            "needs_clarification=%s | follow_up_question=%r | reason=%r | "
            "checked_artifact=%s | raw_response=%s"
        ),
        check_name,
        state.get("question", ""),
        checked_artifact_name,
        bool(parsed_response.get("needs_clarification", False)),
        str(parsed_response.get("follow_up_question", "")),
        str(parsed_response.get("reason", "")),
        truncate_for_log(checked_artifact),
        truncate_for_log(raw_response),
    )

def sql_dialect_for_prompt(state: AgentState) -> str:
    return display_sql_dialect(state.get("sql_dialect", "sqlite"))

def normalize_sql(sql: str) -> str:
    return " ".join(sql.strip().split()).lower()

def extract_sql_from_response(text: str) -> str:
    text = (text or "").strip()

    if "```" in text:
        match = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1).strip()

    upper_text = text.upper()
    start_indexes = [
        index
        for index in (upper_text.find("WITH"), upper_text.find("SELECT"))
        if index != -1
    ]
    if start_indexes:
        text = text[min(start_indexes):]

    return text.strip().rstrip(";").strip()

def schema_for_repair(state: AgentState) -> str:
    return state.get("schema_context") or state.get("extracted_schema", "")

def get_schema_node(state: AgentState) -> AgentState:
    llm = build_llm()

    prompt = thinking_prompts.GET_SCHEMA_PROMPT.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        clarification_block=build_clarification_block(state),
        schema_context=state["schema_context"],
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)

    return {"extracted_schema": content}

def check_schema_clarification_node(state: AgentState) -> AgentState:
    llm = build_llm()
    extracted_schema = state.get("extracted_schema", "")

    prompt = thinking_prompts.CHECK_SCHEMA_CLARIFICATION_PROMPT.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        extracted_schema=extracted_schema,
        thinking_clarification_answer=build_clarification_block(state),
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)
    data = parse_clarification_response(content, "check_schema_clarification")

    log_clarification_check(
        check_name="check_schema_clarification",
        state=state,
        checked_artifact_name="extracted_schema",
        checked_artifact=extracted_schema,
        raw_response=content,
        parsed_response=data,
    )

    return {
        "thinking_needs_clarification": bool(data.get("needs_clarification", False)),
        "thinking_follow_up_question": str(data.get("follow_up_question", "")),
        "thinking_return_to": "get_schema",
    }

def get_tables_columns_node(state: AgentState) -> AgentState:
    llm = build_llm()

    prompt = thinking_prompts.GET_TABLES_COLUMNS_PROMPT.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        clarification_block=build_clarification_block(state),
        extracted_schema=state["extracted_schema"],
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)

    return {"tables_columns": parse_json_artifact(content, "tables_columns")}

def check_tables_columns_clarification_node(state: AgentState) -> AgentState:
    llm = build_llm()
    tables_columns = state.get("tables_columns", {})

    prompt = thinking_prompts.CHECK_TABLES_COLUMNS_CLARIFICATION_PROMPT.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        extracted_schema=state.get("extracted_schema", ""),
        tables_columns=format_artifact(tables_columns),
        thinking_clarification_answer=build_clarification_block(state),
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)
    data = parse_clarification_response(content, "check_tables_columns_clarification")

    log_clarification_check(
        check_name="check_tables_columns_clarification",
        state=state,
        checked_artifact_name="tables_columns",
        checked_artifact=tables_columns,
        raw_response=content,
        parsed_response=data,
    )

    return {
        "thinking_needs_clarification": bool(data.get("needs_clarification", False)),
        "thinking_follow_up_question": str(data.get("follow_up_question", "")),
        "thinking_return_to": "get_tables_columns",
    }

def get_where_conditions_node(state: AgentState) -> AgentState:
    llm = build_llm()

    prompt = thinking_prompts.GET_WHERE_CONDITIONS_PROMPT.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        clarification_block=build_clarification_block(state),
        tables_columns=format_artifact(state["tables_columns"]),
        extracted_schema=state["extracted_schema"],
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)

    return {"where_conditions": parse_json_artifact(content, "where_conditions")}

def check_where_conditions_clarification_node(state: AgentState) -> AgentState:
    llm = build_llm()
    where_conditions = state.get("where_conditions", {})

    prompt = thinking_prompts.CHECK_WHERE_CONDITIONS_CLARIFICATION_PROMPT.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        tables_columns=format_artifact(state.get("tables_columns", {})),
        where_conditions=format_artifact(where_conditions),
        thinking_clarification_answer=build_clarification_block(state),
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)
    data = parse_clarification_response(content, "check_where_conditions_clarification")

    log_clarification_check(
        check_name="check_where_conditions_clarification",
        state=state,
        checked_artifact_name="where_conditions",
        checked_artifact=where_conditions,
        raw_response=content,
        parsed_response=data,
    )

    return {
        "thinking_needs_clarification": bool(data.get("needs_clarification", False)),
        "thinking_follow_up_question": str(data.get("follow_up_question", "")),
        "thinking_return_to": "get_where_conditions",
    }

def get_limit_aggregation_node(state: AgentState) -> AgentState:
    llm = build_llm()

    prompt = thinking_prompts.GET_LIMIT_AGGREGATION_PROMPT.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        clarification_block=build_clarification_block(state),
        tables_columns=format_artifact(state["tables_columns"]),
        where_conditions=format_artifact(state["where_conditions"]),
        extracted_schema=state["extracted_schema"],
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)

    return {"limit_aggregation": parse_json_artifact(content, "limit_aggregation")}

def check_limit_aggregation_clarification_node(state: AgentState) -> AgentState:
    llm = build_llm()
    limit_aggregation = state.get("limit_aggregation", {})

    prompt = thinking_prompts.CHECK_LIMIT_AGGREGATION_CLARIFICATION_PROMPT.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        tables_columns=format_artifact(state.get("tables_columns", {})),
        where_conditions=format_artifact(state.get("where_conditions", {})),
        limit_aggregation=format_artifact(limit_aggregation),
        thinking_clarification_answer=build_clarification_block(state),
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)
    data = parse_clarification_response(content, "check_limit_aggregation_clarification")

    log_clarification_check(
        check_name="check_limit_aggregation_clarification",
        state=state,
        checked_artifact_name="limit_aggregation",
        checked_artifact=limit_aggregation,
        raw_response=content,
        parsed_response=data,
    )

    return {
        "thinking_needs_clarification": bool(data.get("needs_clarification", False)),
        "thinking_follow_up_question": str(data.get("follow_up_question", "")),
        "thinking_return_to": "get_limit_aggregation",
    }

def create_query_node(state: AgentState) -> AgentState:
    llm = build_llm()

    prompt = thinking_prompts.CREATE_QUERY_PROMPT.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        clarification_block=build_clarification_block(state),
        extracted_schema=state["extracted_schema"],
        tables_columns=format_artifact(state["tables_columns"]),
        where_conditions=format_artifact(state["where_conditions"]),
        limit_aggregation=format_artifact(state["limit_aggregation"]),
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)
    sql = extract_sql_from_response(content)

    return {
        "initial_generated_query": sql,
        "generated_query": sql,
        "sql_repair_attempt_count": 0,
        "sql_repair_attempts": [],
    }

def check_query_clarification_node(state: AgentState) -> AgentState:
    llm = build_llm()
    generated_query = state.get("generated_query", "")

    prompt = thinking_prompts.CHECK_QUERY_CLARIFICATION_PROMPT.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        generated_query=generated_query,
        extracted_schema=state.get("extracted_schema", ""),
        thinking_clarification_answer=build_clarification_block(state),
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)
    data = parse_clarification_response(content, "check_query_clarification")

    log_clarification_check(
        check_name="check_query_clarification",
        state=state,
        checked_artifact_name="generated_query",
        checked_artifact=generated_query,
        raw_response=content,
        parsed_response=data,
    )

    return {
        "thinking_needs_clarification": bool(data.get("needs_clarification", False)),
        "thinking_follow_up_question": str(data.get("follow_up_question", "")),
        "thinking_return_to": "create_query",
    }

def verify_query_node(state: AgentState) -> AgentState:
    current_sql = state.get("generated_query", "")
    llm = build_llm()
    prompt = thinking_prompts.VERIFY_QUERY_PROMPT.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        clarification_block=build_clarification_block(state),
        extracted_schema=schema_for_repair(state),
        tables_columns=format_artifact(state.get("tables_columns", {})),
        where_conditions=format_artifact(state.get("where_conditions", {})),
        limit_aggregation=format_artifact(state.get("limit_aggregation", {})),
        generated_query=current_sql,
    )
    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)
    verified_sql = extract_sql_from_response(content)

    return {"generated_query": verified_sql or current_sql}

def validate_query_node(state: AgentState) -> AgentState:
    validation = validate_sql(
        state.get("generated_query", ""),
        dialect=state.get("sql_dialect", "sqlite"),
        schema_context=state.get("schema_context", ""),
    )
    return {"sql_validation": validation}

def route_after_sql_validation(state: AgentState) -> str:
    validation = state.get("sql_validation", {})
    if validation.get("syntax_ok") and validation.get("execution_ok"):
        return "generate_query_answer"

    attempt_count = int(state.get("sql_repair_attempt_count", 0))
    max_attempts = int(state.get("max_sql_repair_attempts", 2))
    if attempt_count >= max_attempts:
        return "generate_query_answer"

    return "repair_query"

def invoke_sql_repair_prompt(prompt: str) -> str:
    llm = build_llm()
    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)
    return extract_sql_from_response(content)

def build_repair_prompt(
    state: AgentState,
    *,
    template: str,
    current_sql: str,
    error_message: str,
) -> str:
    return template.format(
        question=state["question"],
        sql_dialect=sql_dialect_for_prompt(state),
        clarification_block=build_clarification_block(state),
        extracted_schema=schema_for_repair(state),
        generated_query=current_sql,
        error_message=error_message,
    )

def repair_sql_with_fallback(
    state: AgentState,
    current_sql: str,
    error_message: str,
) -> str:
    repaired_sql = invoke_sql_repair_prompt(
        build_repair_prompt(
            state,
            template=thinking_prompts.REPAIR_QUERY_PROMPT,
            current_sql=current_sql,
            error_message=error_message,
        )
    )

    if normalize_sql(repaired_sql) == normalize_sql(current_sql):
        repaired_sql = invoke_sql_repair_prompt(
            build_repair_prompt(
                state,
                template=thinking_prompts.TARGETED_REPAIR_QUERY_PROMPT,
                current_sql=current_sql,
                error_message=error_message,
            )
        )

    if normalize_sql(repaired_sql) == normalize_sql(current_sql):
        repaired_sql = invoke_sql_repair_prompt(
            build_repair_prompt(
                state,
                template=thinking_prompts.REGENERATE_QUERY_PROMPT,
                current_sql=current_sql,
                error_message=error_message,
            )
        )

    return repaired_sql or current_sql

def repair_query_node(state: AgentState) -> AgentState:
    current_sql = state.get("generated_query", "")
    validation = state.get("sql_validation", {})
    error_message = validation_error_message(validation)
    repaired_sql = repair_sql_with_fallback(state, current_sql, error_message)
    repair_validation = validate_sql(
        repaired_sql,
        dialect=state.get("sql_dialect", "sqlite"),
        schema_context=state.get("schema_context", ""),
    )

    attempt = int(state.get("sql_repair_attempt_count", 0)) + 1
    repair_attempts = list(state.get("sql_repair_attempts", []))
    repair_attempts.append(
        {
            "attempt": attempt,
            "input_sql": current_sql,
            "error": error_message,
            "repaired_sql": repaired_sql,
            "no_change": normalize_sql(repaired_sql) == normalize_sql(current_sql),
            "syntax_ok": repair_validation.get("syntax_ok"),
            "syntax_error": repair_validation.get("syntax_error"),
            "execution_ok": repair_validation.get("execution_ok"),
            "execution_error": repair_validation.get("execution_error"),
            "execution_skipped": repair_validation.get("execution_skipped"),
        }
    )

    return {
        "generated_query": repaired_sql,
        "sql_validation": repair_validation,
        "sql_repair_attempt_count": attempt,
        "sql_repair_attempts": repair_attempts,
    }

def route_after_thinking_hitl(state: AgentState) -> str:
    return state.get("thinking_return_to", "get_schema")

def generate_query_answer_node(state: AgentState) -> AgentState:
    return {
        "answer": state.get("generated_query", "")
    }

__all__ = [
    "get_schema_node",
    "check_schema_clarification_node",
    "get_tables_columns_node",
    "check_tables_columns_clarification_node",
    "get_where_conditions_node",
    "check_where_conditions_clarification_node",
    "get_limit_aggregation_node",
    "check_limit_aggregation_clarification_node",
    "create_query_node",
    "check_query_clarification_node",
    "verify_query_node",
    "validate_query_node",
    "route_after_sql_validation",
    "repair_query_node",
    "thinking_hitl_node",
    "route_after_step_for_optional_check",
    "route_after_thinking_check",
    "route_after_thinking_clarification_check",
    "route_after_thinking_hitl",
    "generate_query_answer_node",
]
