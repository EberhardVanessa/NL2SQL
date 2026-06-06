from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Optional



def extract_sql_from_response(text: str) -> str:
    """Extract SQL from model output while preserving existing pipeline behavior."""
    if not text:
        return ""

    text = text.strip()

    if "```" in text:
        match = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            text = match.group(1).strip()

    text = text.strip().rstrip(";").strip()

    upper_text = text.upper()
    start_indexes = [
        index
        for index in (upper_text.find("WITH"), upper_text.find("SELECT"))
        if index != -1
    ]
    if start_indexes:
        text = text[min(start_indexes):]

    return text.strip()


def strip_model_tokens(text: str) -> str:
    for token in ["<turn|>", "<end_of_turn>", "<|eot_id|>", "<|endoftext|>"]:
        text = text.replace(token, "")
    return text.strip()


@dataclass(frozen=True)
class LLMRuntimeConfig:
    engine: str
    provider: str
    api_key: str
    rest_url: str
    rest_timeout: int
    openai_base_url: str
    langgraph_url: str
    langgraph_user_id: str
    langgraph_model: str


class LLMGateway:
    """
    Small provider boundary for the pipeline.

    Providers:
    - local/ollama/openai-compatible/openai: use OpenAI Chat Completions API with OPENAI_BASE_URL.
    - remote/rest/remote_rest: call LLM_REST_URL with {"prompt", "max_new_tokens", "temperature"}.
    - langgraph: call the local schema-agent /ask endpoint with schema_context_base.
    """

    OPENAI_COMPATIBLE_PROVIDERS = {"local", "ollama", "openai-compatible", "openai_compatible", "openai"}
    REST_PROVIDERS = {"remote", "rest", "remote_rest"}
    LANGGRAPH_PROVIDERS = {"langgraph"}

    def __init__(self, config: LLMRuntimeConfig):
        self.config = config
        self.provider = config.provider.lower()
        if self.provider in self.REST_PROVIDERS or self.provider in self.LANGGRAPH_PROVIDERS:
            self.client = None
        elif self.provider in self.OPENAI_COMPATIBLE_PROVIDERS:
            self.client = self._init_openai_client()
        else:
            raise ValueError(
                f"Unsupported LLM_PROVIDER={config.provider!r}. "
                "Use local, remote, or langgraph."
            )

    @property
    def is_langgraph(self) -> bool:
        return self.provider in self.LANGGRAPH_PROVIDERS

    def _init_openai_client(self) -> Any:
        from openai import OpenAI

        return OpenAI(
            api_key=self.config.api_key,
            base_url=self.config.openai_base_url,
        )

    def complete_raw(
        self,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 768,
        stop: Optional[list[str]] = None,
    ) -> str:
        if self.is_langgraph:
            raise ValueError(
                "The langgraph provider requires a question and schema context. "
                "Use complete_sql_with_schema()."
            )
        if self.provider in self.REST_PROVIDERS:
            return self._complete_rest(
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        return self._complete_openai_compatible(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=stop,
        )

    def complete_sql(
        self,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> str:
        raw = self.complete_raw(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            stop=["--", "\n\n\n", "#"] if self.provider not in self.REST_PROVIDERS else None,
        )
        return extract_sql_from_response(raw)

    def complete_sql_with_schema(
        self,
        prompt: str,
        question: str,
        schema_context: str,
        request_id: int | str,
        sql_dialect: str = "SQLite",
        max_sql_repair_attempts: int = 2,
        temperature: float = 0.0,
        max_tokens: int = 512,
    ) -> str:
        if not self.is_langgraph:
            return self.complete_sql(
                prompt=prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        raw = self._complete_langgraph(
            question=question,
            schema_context=schema_context,
            request_id=request_id,
            sql_dialect=sql_dialect,
            max_sql_repair_attempts=max_sql_repair_attempts,
        )
        return extract_sql_from_response(raw)

    def _complete_rest(self, prompt: str, temperature: float, max_tokens: int) -> str:
        import requests

        max_api_retry = 10

        for _ in range(max_api_retry):
            time.sleep(1)
            try:
                response = requests.post(
                    self.config.rest_url,
                    json={
                        "prompt": prompt,
                        "max_new_tokens": max_tokens,
                        "temperature": temperature,
                    },
                    timeout=self.config.rest_timeout,
                )
                response.raise_for_status()
                data = response.json()
                return strip_model_tokens(str(data.get("response", "")))
            except Exception as e:
                print(f"error: {e}")
                time.sleep(3)

        return ""

    def _complete_langgraph(
        self,
        question: str,
        schema_context: str,
        request_id: int | str,
        sql_dialect: str,
        max_sql_repair_attempts: int,
    ) -> str:
        import requests

        max_api_retry = 10
        url = self.config.langgraph_url.rstrip("/")
        if not url.endswith("/ask"):
            url = f"{url}/ask"

        payload = {
            "chat_id": str(request_id),
            "question": question,
            "is_thinking": True,
            "skip_user_interaction": True,
            "sql_dialect": sql_dialect,
            "max_sql_repair_attempts": max_sql_repair_attempts,
            "schema_context_base": schema_context,
        }

        for _ in range(max_api_retry):
            time.sleep(1)
            try:
                response = requests.post(
                    url,
                    json=payload,
                    timeout=self.config.rest_timeout,
                )
                response.raise_for_status()
                data = response.json()
                if data.get("status") == "needs_human":
                    return str(data.get("follow_up_question", ""))
                return strip_model_tokens(str(data.get("answer", "")))
            except Exception as e:
                print(f"error: {e}")
                time.sleep(3)

        return ""

    def _complete_openai_compatible(
        self,
        prompt: str,
        temperature: float,
        max_tokens: int,
        stop: Optional[list[str]],
    ) -> str:
        max_api_retry = 10

        for _ in range(max_api_retry):
            time.sleep(1)
            try:
                response: Any = self.client.chat.completions.create(
                    model=self.config.engine,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=max_tokens,
                    stop=stop,
                    temperature=temperature,
                    top_p=0.95,
                    presence_penalty=0.0,
                    frequency_penalty=0.0,
                )
                return str(response.choices[0].message.content or "").strip()
            except Exception as e:
                print(f"error: {e}")
                time.sleep(3)

        return ""


class SchemaLinkerLLMClient:
    """Adapter matching schema_linker.SchemaLinkerClient."""

    def __init__(self, llm: LLMGateway, temperature: float = 0.0, max_tokens: int = 768):
        self.llm = llm
        self.temperature = temperature
        self.max_tokens = max_tokens

    def complete(self, prompt: str) -> str:
        return self.llm.complete_raw(
            prompt=prompt,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
