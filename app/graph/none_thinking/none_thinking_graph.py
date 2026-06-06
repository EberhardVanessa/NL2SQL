from app.utils import log_node, log_router
from langgraph.graph import StateGraph, START, END

from . import none_thinking_steps as ntg

def add_non_thinking_path(builder: StateGraph) -> None:
    builder.add_node(
        "check_non_thinking_interaction",
        log_node("check_non_thinking_interaction", lambda state: {}),
    )

    builder.add_node("decide", log_node("decide", ntg.decide_node))
    builder.add_node("hitl", log_node("hitl", ntg.hitl_node))
    builder.add_node(
        "generate_answer",
        log_node("generate_answer", ntg.generate_answer_node),
    )

    builder.add_conditional_edges(
        "check_non_thinking_interaction",
        log_router(
            "route_after_non_thinking_start",
            ntg.route_after_non_thinking_start,
        ),
        {
            "decide": "decide",
            "generate_answer": "generate_answer",
        },
    )

    builder.add_conditional_edges(
        "decide",
        log_router("route_after_decide", ntg.route_after_decide),
        {
            "hitl": "hitl",
            "generate_answer": "generate_answer",
        },
    )

    builder.add_edge("hitl", "generate_answer")
    builder.add_edge("generate_answer", END)