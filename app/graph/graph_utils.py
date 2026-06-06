from langgraph.graph import StateGraph, START

from .thinking import thinking_steps as tg
from ..utils import log_node, log_router

def add_entry_nodes(builder: StateGraph) -> None:
    builder.add_node("check_thinking", log_node("check_thinking", lambda state: {}))

    builder.add_edge(START, "check_thinking")

    builder.add_conditional_edges(
        "check_thinking",
        log_router("route_after_thinking_check", tg.route_after_thinking_check),
        {
            "thinking": "get_schema",
            "no_thinking": "check_non_thinking_interaction",
        },
    )