from typing import Any, TypedDict, NotRequired

from app.file.storage import FileRegistry

class AgentState(TypedDict, total=False):
    question: str
    thinking_enabled: NotRequired[bool]
    skip_user_interaction: NotRequired[bool]
    sql_dialect: NotRequired[str]
    schema_context: str

    needs_clarification: NotRequired[bool]
    follow_up_question: NotRequired[str]
    clarification_answer: NotRequired[str]

    extracted_schema: NotRequired[str]
    tables_columns: NotRequired[dict[str, Any]]
    where_conditions: NotRequired[dict[str, Any]]
    limit_aggregation: NotRequired[dict[str, Any]]
    initial_generated_query: NotRequired[str]
    generated_query: NotRequired[str]
    sql_validation: NotRequired[dict[str, Any]]
    sql_repair_attempt_count: NotRequired[int]
    max_sql_repair_attempts: NotRequired[int]
    sql_repair_attempts: NotRequired[list[dict[str, Any]]]

    thinking_needs_clarification: NotRequired[bool]
    thinking_follow_up_question: NotRequired[str]
    thinking_clarification_answer: NotRequired[str]
    thinking_clarification_history: NotRequired[list[dict[str, str]]]
    thinking_return_to: NotRequired[str]

    answer: NotRequired[str]
