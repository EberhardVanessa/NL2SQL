import asyncio
import json
from functools import partial

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.graph.graph import stream_question, stream_resume_question
from app.rest.ask_request import AskRequest


def sse_event(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def emit_status_threadsafe(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, event: dict):
    """
    Thread-safe emitter.

    Safe to call from sync code running inside asyncio.to_thread().
    """
    loop.call_soon_threadsafe(queue.put_nowait, event)


async def run_ask_agent(
    request: AskRequest,
    registry,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
):
    # This is the function ask_question / nodes / routers should call:
    # emit_status({"type": "status", "description": "..."})
    emit_status = partial(emit_status_threadsafe, loop, queue)

    try:
        emit_status(
            {
                "type": "status",
                "description": "Starting schema agent...",
            }
        )

        result = await asyncio.to_thread(
            stream_question,
            thread_id=request.chat_id,
            question=request.question,
            file_registry=registry,
            is_thinking=request.is_thinking,
            skip_user_interaction=request.skip_user_interaction,
            sql_dialect=request.dialect,
            max_sql_repair_attempts=request.max_sql_repair_attempts,
            schema_name=request.schema_name,
            schema_context_base=request.schema_context_base,
            emit_status=emit_status,
        )
        if result.get("status") == "needs_human":
            emit_status(
                {
                    "type": "needs_human",
                    "follow_up_question": result.get(
                        "follow_up_question",
                        "Please clarify.",
                    ),
                }
            )
        else:
            emit_status(
                {
                    "type": "final",
                    "result": result,
                    "answer": result.get("answer", "No answer returned."),
                }
            )

    except Exception as e:
        emit_status(
            {
                "type": "error",
                "message": str(e),
            }
        )

    finally:
        emit_status({"type": "done"})


async def ask_event_stream(
    request: AskRequest,
    registry,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
):
    task = asyncio.create_task(
        run_ask_agent(
            request=request,
            registry=registry,
            loop=loop,
            queue=queue,
        )
    )

    try:
        while True:
            event = await queue.get()

            if event.get("type") == "done":
                yield "data: [DONE]\n\n"
                break

            yield sse_event(event)

    finally:
        if not task.done():
            task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass


async def ask_events_service(request: AskRequest, registry):
    schema_context = registry.combined_text().strip()

    request_schema_context = (request.schema_context_base or "").strip()
    if not schema_context and not request_schema_context:
        raise HTTPException(
            status_code=400,
            detail="No active schema files are uploaded and no schema_context_base was provided.",
        )

    print("ASK EVENTS resolved thread_id:", request.chat_id)

    queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    return StreamingResponse(
        ask_event_stream(
            request=request,
            registry=registry,
            loop=loop,
            queue=queue,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def run_resume_agent(
    request,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
):
    emit_status = partial(emit_status_threadsafe, loop, queue)

    try:
        emit_status(
            {
                "type": "status",
                "description": "Resuming schema agent...",
            }
        )

        result = await asyncio.to_thread(
            stream_resume_question,
            thread_id=request.chat_id,
            human_answer=request.human_answer,
            emit_status=emit_status,
        )
        if result.get("status") == "needs_human":
            emit_status(
                {
                    "type": "needs_human",
                    "follow_up_question": result.get(
                        "follow_up_question",
                        "Please clarify.",
                    ),
                }
            )
        else:
            emit_status(
                {
                    "type": "final",
                    "result": result,
                    "answer": result.get("answer", "No answer returned."),
                }
            )

    except Exception as e:
        emit_status(
            {
                "type": "error",
                "message": str(e),
            }
        )

    finally:
        emit_status({"type": "done"})


async def resume_event_stream(
    request,
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
):
    task = asyncio.create_task(
        run_resume_agent(
            request=request,
            loop=loop,
            queue=queue,
        )
    )

    try:
        while True:
            event = await queue.get()

            if event.get("type") == "done":
                yield "data: [DONE]\n\n"
                break

            yield sse_event(event)

    finally:
        if not task.done():
            task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass


async def resume_events_service(request):
    queue: asyncio.Queue[dict] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    return StreamingResponse(
        resume_event_stream(
            request=request,
            loop=loop,
            queue=queue,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
