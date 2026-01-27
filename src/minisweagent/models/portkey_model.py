import logging
import os
from pathlib import Path
from typing import Any, Literal

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
from minisweagent.models.litellm_model import _response_to_dict
from minisweagent.models.utils.cache_control import set_cache_control

logger = logging.getLogger("portkey_model")

try:
    from portkey_ai import Portkey
except ImportError:
    raise ImportError(
        "The portkey-ai package is required to use PortkeyModel. Please install it with: pip install portkey-ai"
    )


class PortkeyModelConfig(BaseModel):
    model_name: str
    model_kwargs: dict[str, Any] = {}
    provider: str = ""
    """The LLM provider to use (e.g., 'openai', 'anthropic', 'google').
    If not specified, will be auto-detected from model_name.
    Required by Portkey when not using a virtual key.
    """
    litellm_model_registry: Path | str | None = os.getenv("LITELLM_MODEL_REGISTRY_PATH")
    """Legacy option for model registry overrides (unused in token-only mode)."""
    litellm_model_name_override: str = ""
    """Legacy option for model name overrides (unused in token-only mode)."""
    set_cache_control: Literal["default_end"] | None = None
    """Set explicit cache control markers, for example for Anthropic models"""
    cost_tracking: Literal["default", "ignore_errors"] = os.getenv("MSWEA_COST_TRACKING", "default")
    """Legacy cost tracking mode (ignored in token-only mode)."""
    billing: dict[str, Any] | None = None


class PortkeyModel:
    def __init__(self, *, config_class: type = PortkeyModelConfig, **kwargs):
        self.config = config_class(**kwargs)
        self.cost = 0.0
        self.n_calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.billing_mode = ""
        self._last_attempt_prompt_tokens: int | None = None
        self._token_tracker = TokenTracker(
            model_name=self.config.model_name,
            billing=self.config.billing,
        )

        # Get API key from environment or raise error
        self._api_key = os.getenv("PORTKEY_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Portkey API key is required. Set it via the "
                "PORTKEY_API_KEY environment variable. You can permanently set it with "
                "`mini-extra config set PORTKEY_API_KEY YOUR_KEY`."
            )

        # Get virtual key from environment
        virtual_key = os.getenv("PORTKEY_VIRTUAL_KEY")

        # Initialize Portkey client
        client_kwargs = {"api_key": self._api_key}
        if virtual_key:
            client_kwargs["virtual_key"] = virtual_key
        elif self.config.provider:
            # If no virtual key but provider is specified, pass it
            client_kwargs["provider"] = self.config.provider

        self.client = Portkey(**client_kwargs)

    @retry(
        reraise=True,
        stop=stop_after_attempt(int(os.getenv("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "3"))),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        retry=retry_if_not_exception_type((KeyboardInterrupt, TypeError, ValueError)),
    )
    def _query(self, messages: list[dict[str, str]], **kwargs):
        self._last_attempt_prompt_tokens = self._token_tracker.add_attempt(messages=messages)
        # return self.client.with_options(metadata={"request_id": request_id}).chat.completions.create(
        return self.client.chat.completions.create(
            model=self.config.model_name,
            messages=messages,
            **(self.config.model_kwargs | kwargs),
        )

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        if self.config.set_cache_control:
            messages = set_cache_control(messages, mode=self.config.set_cache_control)
        payload_messages = [{"role": msg["role"], "content": msg["content"]} for msg in messages]
        response = self._query(payload_messages, **kwargs)
        response_dict = _response_to_dict(response)
        content = response.choices[0].message.content or ""
        call_stats = self._token_tracker.add_call(
            messages=payload_messages,
            response=response_dict,
            completion_text=content,
            attempt_prompt_tokens=self._last_attempt_prompt_tokens,
        )
        self.n_calls += 1
        self.prompt_tokens = self._token_tracker.prompt_tokens
        self.completion_tokens = self._token_tracker.completion_tokens
        self.total_tokens = self._token_tracker.total_tokens
        self.billing_mode = self._token_tracker.summary().get("billing_mode", "")
        GLOBAL_TOKEN_STATS.add(call_stats.get("total_tokens", 0))
        return {
            "content": content,
            "extra": {
                "response": response_dict,
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
