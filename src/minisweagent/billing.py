"""Token tracking helpers for runtime usage accounting."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("billing")

_WARNED_MISSING_USAGE: set[str] = set()
_WARNED_ESTIMATE_FAIL: set[str] = set()


def _extract_usage(response: dict[str, Any]) -> tuple[int, int, int] | None:
    usage = response.get("usage") or {}
    if not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    total = usage.get("total_tokens")
    if prompt is None and completion is None and total is None:
        return None
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    if prompt is None and total is not None and completion is not None:
        prompt = total - completion
    if completion is None and total is not None and prompt is not None:
        completion = total - prompt
    if prompt is None or completion is None or total is None:
        return None
    return int(prompt), int(completion), int(total)


def _get_encoding(name: str):
    try:
        import tiktoken
    except ImportError as exc:  # pragma: no cover - dependency may be absent
        raise RuntimeError("tiktoken is required for token estimate mode") from exc
    return tiktoken.get_encoding(name)


def _estimate_tokens(
    messages: list[dict[str, str]], completion_text: str, estimate_cfg: dict[str, Any]
) -> tuple[int, int, int]:
    tokenizer_name = str(estimate_cfg.get("tokenizer", "tiktoken"))
    if tokenizer_name.lower() != "tiktoken":
        raise ValueError(f"Unsupported tokenizer for token estimate: {tokenizer_name}")
    encoding_name = str(estimate_cfg.get("encoding", "cl100k_base"))
    encoding = _get_encoding(encoding_name)
    overhead = estimate_cfg.get("message_overhead", {}) if isinstance(estimate_cfg.get("message_overhead"), dict) else {}
    per_message = int(overhead.get("per_message", 3))
    per_name = int(overhead.get("per_name", 1))

    prompt_tokens = 0
    for message in messages:
        prompt_tokens += per_message
        for key, value in message.items():
            prompt_tokens += len(encoding.encode(str(value or "")))
            if key == "name":
                prompt_tokens += per_name
    prompt_tokens += 3  # priming

    completion_tokens = len(encoding.encode(completion_text or ""))
    total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


def _estimate_prompt_tokens(messages: list[dict[str, str]], estimate_cfg: dict[str, Any]) -> int:
    prompt_tokens, _, _ = _estimate_tokens(messages, "", estimate_cfg)
    return prompt_tokens


class TokenTracker:
    def __init__(
        self,
        *,
        model_name: str,
        billing: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        self.model_name = model_name
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.api_calls = 0
        self.billed_prompt_tokens = 0
        self.billed_completion_tokens = 0
        self.billed_tokens = 0
        self._estimate_failed = False

        billing = billing if isinstance(billing, dict) else {}
        self._billing_mode = str(billing.get("mode", "auto"))
        self._estimate_cfg = billing.get("estimate", {}) if isinstance(billing.get("estimate"), dict) else {}
        self._resolved_mode = ""

    def add_attempt(self, *, messages: list[dict[str, str]]) -> int:
        """Record a prompt-only token estimate for a single model attempt."""
        try:
            prompt_tokens = _estimate_prompt_tokens(messages, self._estimate_cfg)
        except Exception as exc:
            prompt_tokens = 0
            if self.model_name not in _WARNED_ESTIMATE_FAIL:
                _WARNED_ESTIMATE_FAIL.add(self.model_name)
                logger.warning("Token estimate failed for model '%s': %s", self.model_name, exc)

        self.billed_prompt_tokens += prompt_tokens
        self.billed_tokens = self.billed_prompt_tokens + self.billed_completion_tokens
        return prompt_tokens

    def add_call(
        self,
        *,
        messages: list[dict[str, str]],
        response: dict[str, Any],
        completion_text: str,
        attempt_prompt_tokens: int | None = None,
    ) -> dict[str, Any]:
        self.api_calls += 1
        usage = _extract_usage(response)

        mode = self._billing_mode
        if mode == "auto":
            mode = "usage" if usage is not None else "estimate"
        elif mode == "usage" and usage is None:
            mode = "estimate"

        if mode == "usage" and usage is not None:
            prompt_tokens, completion_tokens, total_tokens = usage
        elif mode == "estimate":
            if self._estimate_failed:
                prompt_tokens = completion_tokens = total_tokens = 0
                mode = "none"
            else:
                try:
                    prompt_tokens, completion_tokens, total_tokens = _estimate_tokens(
                        messages, completion_text, self._estimate_cfg
                    )
                except Exception as exc:
                    prompt_tokens = completion_tokens = total_tokens = 0
                    mode = "none"
                    self._estimate_failed = True
                    if self.model_name not in _WARNED_ESTIMATE_FAIL:
                        _WARNED_ESTIMATE_FAIL.add(self.model_name)
                        logger.warning("Token estimate failed for model '%s': %s", self.model_name, exc)
        else:
            prompt_tokens = completion_tokens = total_tokens = 0
            mode = "none"

        if usage is None and mode in {"estimate", "none"} and self.model_name not in _WARNED_MISSING_USAGE:
            _WARNED_MISSING_USAGE.add(self.model_name)
            logger.warning("Model '%s' returned no usage; token estimate mode enabled", self.model_name)

        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens
        if attempt_prompt_tokens is None:
            self.billed_prompt_tokens += prompt_tokens
        else:
            self.billed_prompt_tokens += prompt_tokens - attempt_prompt_tokens
        self.billed_completion_tokens += completion_tokens
        self.billed_tokens = self.billed_prompt_tokens + self.billed_completion_tokens
        if mode in {"usage", "estimate", "none"}:
            self._resolved_mode = mode

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": 0.0,
            "billing_mode": mode,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "trace_tokens": self.total_tokens,
            "billed_tokens": self.billed_tokens,
            "cost_usd": 0.0,
            "api_calls": self.api_calls,
            "billing_mode": self._resolved_mode or self._billing_mode,
        }


class BillingTracker(TokenTracker):
    def __init__(self, *, model_name: str, pricing: dict[str, Any] | None = None, billing: dict[str, Any] | None = None):
        super().__init__(model_name=model_name, billing=billing)
