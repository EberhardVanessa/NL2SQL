from langgraph.graph import StateGraph, START, END
from . import thinking_steps as tg

from app.utils import log_node, log_router


def add_thinking_nodes(builder: StateGraph) -> None:
    builder.add_node("get_schema", log_node("get_schema", tg.get_schema_node))
    builder.add_node(
        "check_schema_clarification",
        log_node("check_schema_clarification", tg.check_schema_clarification_node),
    )

    builder.add_node(
        "get_tables_columns",
        log_node("get_tables_columns", tg.get_tables_columns_node),
    )
    builder.add_node(
        "check_tables_columns_clarification",
        log_node(
            "check_tables_columns_clarification",
            tg.check_tables_columns_clarification_node,
        ),
    )

    builder.add_node(
        "get_where_conditions",
        log_node("get_where_conditions", tg.get_where_conditions_node),
    )
    builder.add_node(
        "check_where_conditions_clarification",
        log_node(
            "check_where_conditions_clarification",
            tg.check_where_conditions_clarification_node,
        ),
    )

    builder.add_node(
        "get_limit_aggregation",
        log_node("get_limit_aggregation", tg.get_limit_aggregation_node),
    )
    builder.add_node(
        "check_limit_aggregation_clarification",
        log_node(
            "check_limit_aggregation_clarification",
            tg.check_limit_aggregation_clarification_node,
        ),
    )

    builder.add_node("create_query", log_node("create_query", tg.create_query_node))
    builder.add_node(
        "verify_query",
        log_node("verify_query", tg.verify_query_node),
    )
    builder.add_node(
        "validate_query",
        log_node("validate_query", tg.validate_query_node),
    )
    builder.add_node(
        "repair_query",
        log_node("repair_query", tg.repair_query_node),
    )

    builder.add_node(
        "thinking_hitl",
        log_node("thinking_hitl", tg.thinking_hitl_node),
    )
    builder.add_node(
        "generate_query_answer",
        log_node("generate_query_answer", tg.generate_query_answer_node),
    )

def add_thinking_step(
    builder: StateGraph,
    *,
    step_node: str,
    check_node: str,
    next_node: str,
    optional_check_router_name: str,
    clarification_router_name: str,
) -> None:
    builder.add_conditional_edges(
        step_node,
        log_router(
            optional_check_router_name,
            tg.route_after_step_for_optional_check,
        ),
        {
            "check": check_node,
            "skip_check": next_node,
        },
    )

    builder.add_conditional_edges(
        check_node,
        log_router(
            clarification_router_name,
            tg.route_after_thinking_clarification_check,
        ),
        {
            "thinking_hitl": "thinking_hitl",
            "continue": next_node,
        },
    )

def add_thinking_edges(builder: StateGraph) -> None:
    add_thinking_step(
        builder,
        step_node="get_schema",
        check_node="check_schema_clarification",
        next_node="get_tables_columns",
        optional_check_router_name="route_after_get_schema_optional_check",
        clarification_router_name="route_after_schema_clarification_check",
    )

    add_thinking_step(
        builder,
        step_node="get_tables_columns",
        check_node="check_tables_columns_clarification",
        next_node="get_where_conditions",
        optional_check_router_name="route_after_get_tables_columns_optional_check",
        clarification_router_name="route_after_tables_columns_clarification_check",
    )

    add_thinking_step(
        builder,
        step_node="get_where_conditions",
        check_node="check_where_conditions_clarification",
        next_node="get_limit_aggregation",
        optional_check_router_name="route_after_get_where_conditions_optional_check",
        clarification_router_name="route_after_where_conditions_clarification_check",
    )

    add_thinking_step(
        builder,
        step_node="get_limit_aggregation",
        check_node="check_limit_aggregation_clarification",
        next_node="create_query",
        optional_check_router_name="route_after_get_limit_aggregation_optional_check",
        clarification_router_name="route_after_limit_aggregation_clarification_check",
    )

    builder.add_edge("create_query", "verify_query")

    builder.add_edge("verify_query", "validate_query")
    builder.add_conditional_edges(
        "validate_query",
        log_router("route_after_validate_query", tg.route_after_sql_validation),
        {
            "repair_query": "repair_query",
            "generate_query_answer": "generate_query_answer",
        },
    )
    builder.add_conditional_edges(
        "repair_query",
        log_router("route_after_repair_query", tg.route_after_sql_validation),
        {
            "repair_query": "repair_query",
            "generate_query_answer": "generate_query_answer",
        },
    )

    builder.add_conditional_edges(
        "thinking_hitl",
        log_router("route_after_thinking_hitl", tg.route_after_thinking_hitl),
        {
            "get_schema": "get_schema",
            "get_tables_columns": "get_tables_columns",
            "get_where_conditions": "get_where_conditions",
            "get_limit_aggregation": "get_limit_aggregation",
            "create_query": "create_query",
        },
    )

    builder.add_edge("generate_query_answer", END)

def add_thinking_path(builder: StateGraph) -> None:
    add_thinking_nodes(builder)
    add_thinking_edges(builder)
