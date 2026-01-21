import json
import logging
import os
from typing import Any

import requests
from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from minisweagent.models import GLOBAL_MODEL_STATS

logger = logging.getLogger("chatanywhere_model")


class ChatAnywhereModelConfig(BaseModel):
    model_name: str
    model_kwargs: dict[str, Any] = {}
    api_url: str | None = None


class ChatAnywhereAPIError(Exception):
    pass


class ChatAnywhereAuthenticationError(Exception):
    pass


class ChatAnywhereRateLimitError(Exception):
    pass


class ChatAnywhereModel:
    def __init__(self, **kwargs):
        self.config = ChatAnywhereModelConfig(**kwargs)
        self.cost = 0.0
        self.n_calls = 0
        self._api_url = self._resolve_api_url()
        self._api_key = os.getenv("OPENAI_API_KEY", "")

    def _resolve_api_url(self) -> str:
        if self.config.api_url:
            return self.config.api_url
        base = os.getenv("OPENAI_API_BASE") or os.getenv("OPENAI_BASE_URL") or ""
        base = base.strip()
        if not base:
            raise ChatAnywhereAPIError("OPENAI_API_BASE (or OPENAI_BASE_URL) must be set")
        if base.endswith("/chat/completions"):
            return base
        return base.rstrip("/") + "/chat/completions"

    @retry(
        reraise=True,
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        retry=retry_if_not_exception_type((ChatAnywhereAuthenticationError, KeyboardInterrupt)),
    )
    def _query(self, messages: list[dict[str, str]], **kwargs):
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.config.model_name,
            "messages": messages,
            **(self.config.model_kwargs | kwargs),
        }

        try:
            response = requests.post(self._api_url, headers=headers, data=json.dumps(payload), timeout=60)
            if response.status_code == 401:
                raise ChatAnywhereAuthenticationError(
                    "Authentication failed. Check OPENAI_API_KEY or update run config."
                )
            if response.status_code == 429:
                raise ChatAnywhereRateLimitError("Rate limit exceeded")
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as exc:
            raise ChatAnywhereAPIError(f"HTTP {response.status_code}: {response.text}") from exc
        except requests.exceptions.RequestException as exc:
            raise ChatAnywhereAPIError(f"Request failed: {exc}") from exc

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        response = self._query([{"role": msg["role"], "content": msg["content"]} for msg in messages], **kwargs)

        usage = response.get("usage", {})
        cost = usage.get("cost", 0.0) or 0.0
        self.n_calls += 1
        self.cost += cost
        GLOBAL_MODEL_STATS.add(cost)

        return {
            "content": response.get("choices", [{}])[0].get("message", {}).get("content", "") or "",
            "extra": {"response": response},
        }

    def get_template_vars(self) -> dict[str, Any]:
        return self.config.model_dump() | {"n_model_calls": self.n_calls, "model_cost": self.cost}
