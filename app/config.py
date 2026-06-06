from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "http://host.docker.internal:11434/v1")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "ollama")
    model_name: str = os.getenv("MODEL_NAME", "llama3.1:8b")
    data_dir: str = os.getenv("DATA_DIR", "/data")
    upload_dir: str = os.getenv("UPLOAD_DIR", "/data/uploads")
    registry_path: str = os.getenv("REGISTRY_PATH", "/data/file_registry.json")
    llm_provider: str = os.getenv("LLM_PROVIDER", "local")
    llm_rest_url: str = os.getenv("LLM_REST_URL", "http://127.0.0.1:8000/generate")
    llm_max_new_tokens: int = os.getenv("LLM_MAX_NEW_TOKENS", 2000)
    llm_temperature: float = os.getenv("LLM_TEMPERATURE", 0.0)
    llm_timeout: int = os.getenv("LLM_TIMEOUT", 120)


settings = Settings()
