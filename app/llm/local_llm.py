from app.config import settings
from langchain_openai import ChatOpenAI

def build_local_llm() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key,
        model=settings.model_name,
        temperature=0,
    )