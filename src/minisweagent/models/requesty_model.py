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

from minisweagent.billing import TokenTracker
from minisweagent.models import GLOBAL_TOKEN_STATS

logger = logging.getLogger("requesty_model")


class RequestyModelConfig(BaseModel):
    model_name: str
    model_kwargs: dict[str, Any] = {}
    billing: dict[str, Any] | None = None


class RequestyAPIError(Exception):
    """Custom exception for Requesty API errors."""

    pass


class RequestyAuthenticationError(Exception):
    """Custom exception for Requesty authentication errors."""

    pass


class RequestyRateLimitError(Exception):
    """Custom exception for Requesty rate limit errors."""

    pass


class RequestyModel:
    def __init__(self, **kwargs):
        self.config = RequestyModelConfig(**kwargs)
        self.cost = 0.0
        self.n_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.billing_mode = ""
        self._last_attempt_prompt_tokens: int | None = None
        self._api_url = "https://router.requesty.ai/v1/chat/completions"
        self._api_key = os.getenv("REQUESTY_API_KEY", "")
        self._token_tracker = TokenTracker(
            model_name=self.config.model_name,
            billing=self.config.billing,
        )

    @retry(
        reraise=True,
        stop=stop_after_attempt(int(os.getenv("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "3"))),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        retry=retry_if_not_exception_type(
            (
                RequestyAuthenticationError,
                KeyboardInterrupt,
            )
        ),
    )
    def _query(self, messages: list[dict[str, str]], **kwargs):
        self._last_attempt_prompt_tokens = self._token_tracker.add_attempt(messages=messages)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/SWE-agent/mini-swe-agent",
            "X-Title": "mini-swe-agent",
        }

        payload = {
            "model": self.config.model_name,
            "messages": messages,
            **(self.config.model_kwargs | kwargs),
        }

        try:
            response = requests.post(self._api_url, headers=headers, data=json.dumps(payload), timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if response.status_code == 401:
                error_msg = "Authentication failed. You can permanently set your API key with `mini-extra config set REQUESTY_API_KEY YOUR_KEY`."
                raise RequestyAuthenticationError(error_msg) from e
            elif response.status_code == 429:
                raise RequestyRateLimitError("Rate limit exceeded") from e
            else:
                raise RequestyAPIError(f"HTTP {response.status_code}: {response.text}") from e
        except requests.exceptions.RequestException as e:
            raise RequestyAPIError(f"Request failed: {e}") from e

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        response = self._query([{"role": msg["role"], "content": msg["content"]} for msg in messages], **kwargs)
        call_stats = self._token_tracker.add_call(
            messages=[{"role": msg["role"], "content": msg["content"]} for msg in messages],
            response=response,
            completion_text=response.get("choices", [{}])[0].get("message", {}).get("content", "") or "",
            attempt_prompt_tokens=self._last_attempt_prompt_tokens,
        )
        self.n_calls += 1
        self.prompt_tokens = self._token_tracker.prompt_tokens
        self.completion_tokens = self._token_tracker.completion_tokens
        self.total_tokens = self._token_tracker.total_tokens
        self.billing_mode = self._token_tracker.summary().get("billing_mode", "")
        GLOBAL_TOKEN_STATS.add(call_stats.get("total_tokens", 0))

        return {
            "content": response["choices"][0]["message"]["content"] or "",
            "extra": {
                "response": response,  # already is json
            },
        }

    def get_template_vars(self) -> dict[str, Any]:
        return self.config.model_dump() | {
            "n_model_calls": self.n_calls,
            "model_cost": self.cost,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

    def get_billing_stats(self) -> dict[str, Any]:
        return self._token_tracker.summary()
