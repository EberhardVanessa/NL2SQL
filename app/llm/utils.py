from app.config import settings
from app.llm.local_llm import build_local_llm
from app.llm.rest_llm import build_rest_llm


def build_llm():
    provider = settings.llm_provider.strip().lower()

    if provider == "local":
        return build_local_llm()

    if provider == "remote":
        return build_rest_llm()

    raise ValueError(
        f"Unsupported LLM_PROVIDER={settings.llm_provider!r}. "
        "Use 'local' or 'remote'."
    )