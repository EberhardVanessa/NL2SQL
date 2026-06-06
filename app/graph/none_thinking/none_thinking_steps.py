from app.graph.agent_state import AgentState
from app.llm.utils import build_llm
from app.graph.none_thinking.none_thinking_prompts import DECIDE_PROMPT, GENERATE_ANSWER_PROMPT

from langgraph.types import interrupt

import json

from app.graph.sql_validation import display_sql_dialect


def decide_node(state: AgentState) -> AgentState:
    llm = build_llm()

    prompt = DECIDE_PROMPT.format(
        question=state["question"],
        sql_dialect=display_sql_dialect(state.get("sql_dialect", "sqlite")),
        schema_context=state["schema_context"],
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)

    try:
        data = json.loads(content)
    except Exception:
        data = {
            "needs_clarification": False,
            "follow_up_question": "",
        }

    return {
        "needs_clarification": bool(data.get("needs_clarification", False)),
        "follow_up_question": str(data.get("follow_up_question", "")),
    }


def hitl_node(state: AgentState) -> AgentState:
    if state.get("needs_clarification"):
        answer = interrupt(
            {
                "type": "clarification_request",
                "question": state.get("follow_up_question", "Please clarify your request."),
            }
        )
        return {"clarification_answer": str(answer)}
    return {}


def generate_answer_node(state: AgentState) -> AgentState:
    llm = build_llm()

    clarification = state.get("clarification_answer", "").strip()
    clarification_block = (
        f"\nUser clarification:\n{clarification}\n" if clarification else "\n"
    )

    prompt = GENERATE_ANSWER_PROMPT.format(
        question=state["question"],
        sql_dialect=display_sql_dialect(state.get("sql_dialect", "sqlite")),
        clarification_block=clarification_block,
        schema_context=state["schema_context"],
    )

    response = llm.invoke(prompt)
    content = response.content if isinstance(response.content, str) else str(response.content)
    return {"answer": content}


def route_after_decide(state: AgentState) -> str:
    if state.get("skip_user_interaction", False):
        return "generate_answer"

    if state.get("needs_clarification"):
        return "hitl"

    return "generate_answer"

def route_after_non_thinking_start(state: AgentState) -> str:
    if state.get("skip_user_interaction", False):
        return "generate_answer"

    return "decide"

__all__ = [
    "decide_node",
    "hitl_node",
    "generate_answer_node",
    "route_after_decide",
    "route_after_non_thinking_start",
]
