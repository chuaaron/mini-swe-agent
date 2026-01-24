import logging
from collections.abc import Callable

import litellm
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from minisweagent.models import GLOBAL_TOKEN_STATS
from minisweagent.models.litellm_model import (
    LitellmModel,
    LitellmModelConfig,
    _response_to_dict,
)
from minisweagent.models.utils.openai_utils import coerce_responses_text

logger = logging.getLogger("litellm_response_api_model")


class LitellmResponseAPIModelConfig(LitellmModelConfig):
    pass


class LitellmResponseAPIModel(LitellmModel):
    def __init__(self, *, config_class: Callable = LitellmResponseAPIModelConfig, **kwargs):
        super().__init__(config_class=config_class, **kwargs)
        self._previous_response_id: str | None = None

    @retry(
        reraise=True,
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        retry=retry_if_not_exception_type(
            (
                litellm.exceptions.UnsupportedParamsError,
                litellm.exceptions.NotFoundError,
                litellm.exceptions.PermissionDeniedError,
                litellm.exceptions.ContextWindowExceededError,
                litellm.exceptions.APIError,
                litellm.exceptions.AuthenticationError,
                KeyboardInterrupt,
            )
        ),
    )
    def _query(self, messages: list[dict[str, str]], **kwargs):
        try:
            # Remove 'timestamp' field added by agent - not supported by OpenAI responses API
            clean_messages = [{"role": msg["role"], "content": msg["content"]} for msg in messages]
            resp = litellm.responses(
                model=self.config.model_name,
                input=clean_messages if self._previous_response_id is None else clean_messages[-1:],
                previous_response_id=self._previous_response_id,
                **(self.config.model_kwargs | kwargs),
            )
            self._previous_response_id = getattr(resp, "id", None)
            return resp
        except litellm.exceptions.AuthenticationError as e:
            e.message += " You can permanently set your API key with `mini-extra config set KEY VALUE`."
            raise e

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        previous_response_id = self._previous_response_id
        response = self._query(messages, **kwargs)
        text = coerce_responses_text(response)
        response_dict = _response_to_dict(response)
        payload_messages = [{"role": msg["role"], "content": msg["content"]} for msg in messages]
        if previous_response_id is not None:
            payload_messages = payload_messages[-1:]
        call_stats = self._token_tracker.add_call(
            messages=payload_messages,
            response=response_dict,
            completion_text=text,
        )
        self.prompt_tokens = self._token_tracker.prompt_tokens
        self.completion_tokens = self._token_tracker.completion_tokens
        self.total_tokens = self._token_tracker.total_tokens
        self.billing_mode = self._token_tracker.summary().get("billing_mode", "")
        self.n_calls += 1
        GLOBAL_TOKEN_STATS.add(call_stats.get("total_tokens", 0))
        return {
            "content": text,
        }
