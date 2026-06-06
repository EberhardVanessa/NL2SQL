from dataclasses import dataclass
from typing import Any

import requests
from app.config import settings

def strip_model_tokens(text: str) -> str:
    for token in ["<turn|>", "<end_of_turn>", "<|eot_id|>", "<|endoftext|>"]:
        text = text.replace(token, "")
    return text.strip()

@dataclass
class RestLLMResponse:
    content: str


class RestLLM:
    def __init__(
        self,
        url: str,
        max_new_tokens: int = 2000,
        temperature: float = 0.3,
        timeout: int = 120,
    ):
        self.url = url
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.timeout = timeout

    def invoke(self, prompt: str, **kwargs: Any) -> RestLLMResponse:
        body = {
            "prompt": prompt,
            "max_new_tokens": kwargs.get("max_new_tokens", self.max_new_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }

        response = requests.post(
            self.url,
            json=body,
            timeout=self.timeout,
        )
        response.raise_for_status()

        data = response.json()

        return RestLLMResponse(
            content=strip_model_tokens(str(data.get("response", "")))
        )


def build_rest_llm() -> RestLLM:
    return RestLLM(
        url=settings.llm_rest_url,
        max_new_tokens=settings.llm_max_new_tokens,
        temperature=settings.llm_temperature,
        timeout=settings.llm_timeout,
    )