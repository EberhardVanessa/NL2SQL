from fastapi import HTTPException

from app.graph.graph import ask_question


def ask_service(request, registry):
    schema_context = registry.combined_text().strip()
    request_schema_context = (request.schema_context_base or "").strip()
    if not schema_context and not request_schema_context:
        raise HTTPException(
            status_code=400,
            detail="No active schema files are uploaded and no schema_context_base was provided.",
        )

    print("ASK resolved thread_id:", request.chat_id)

    result = ask_question(
        thread_id=request.chat_id,
        question=request.question,
        file_registry=registry,
        is_thinking=request.is_thinking,
        skip_user_interaction=request.skip_user_interaction,
        sql_dialect=request.dialect,
        max_sql_repair_attempts=request.max_sql_repair_attempts,
        schema_name=request.schema_name,
        schema_context_base=request.schema_context_base,
    )

    return result
