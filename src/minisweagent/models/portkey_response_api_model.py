import logging
import os

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from minisweagent.models import GLOBAL_TOKEN_STATS
from minisweagent.models.litellm_model import _response_to_dict
from minisweagent.models.portkey_model import PortkeyModel, PortkeyModelConfig
from minisweagent.models.utils.cache_control import set_cache_control
from minisweagent.models.utils.openai_utils import coerce_responses_text

logger = logging.getLogger("portkey_response_api_model")


class PortkeyResponseAPIModelConfig(PortkeyModelConfig):
    pass


class PortkeyResponseAPIModel(PortkeyModel):
    def __init__(self, *, config_class: type = PortkeyResponseAPIModelConfig, **kwargs):
        super().__init__(config_class=config_class, **kwargs)
        self._previous_response_id: str | None = None

    @retry(
        reraise=True,
        stop=stop_after_attempt(int(os.getenv("MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT", "10"))),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        retry=retry_if_not_exception_type((KeyboardInterrupt, TypeError, ValueError)),
    )
    def _query(self, messages: list[dict[str, str]], **kwargs):
        input_messages = messages if self._previous_response_id is None else messages[-1:]
        resp = self.client.responses.create(
            model=self.config.model_name,
            input=input_messages,
            previous_response_id=self._previous_response_id,
            **(self.config.model_kwargs | kwargs),
        )
        self._previous_response_id = getattr(resp, "id", None)
        return resp

    def query(self, messages: list[dict[str, str]], **kwargs) -> dict:
        if self.config.set_cache_control:
            messages = set_cache_control(messages, mode=self.config.set_cache_control)
        previous_response_id = self._previous_response_id
        response = self._query(messages, **kwargs)
        text = coerce_responses_text(response)
        response_dict = _response_to_dict(response)
        payload_messages = messages if previous_response_id is None else messages[-1:]
        call_stats = self._token_tracker.add_call(
            messages=payload_messages,
            response=response_dict,
            completion_text=text,
        )
        self.n_calls += 1
        self.prompt_tokens = self._token_tracker.prompt_tokens
        self.completion_tokens = self._token_tracker.completion_tokens
        self.total_tokens = self._token_tracker.total_tokens
        self.billing_mode = self._token_tracker.summary().get("billing_mode", "")
        GLOBAL_TOKEN_STATS.add(call_stats.get("total_tokens", 0))
        return {
            "content": text,
            "extra": {
                "response": response_dict,
            },
        }
