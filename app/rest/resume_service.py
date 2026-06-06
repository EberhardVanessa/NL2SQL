from app.graph.graph import resume_question


def resume_service(request):
    print("RESUME resolved thread_id:", request.chat_id)

    result = resume_question(
        thread_id=request.chat_id,
        human_answer=request.human_answer,
    )

    return result
