"""Billing helpers for token/cost tracking."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("billing")

_MODEL_MODE_CACHE: dict[str, str] = {}
_WARNED_MISSING_USAGE: set[str] = set()
_WARNED_MISSING_PRICING: set[str] = set()


def _resolve_pricing(model_name: str, pricing: dict[str, Any] | None) -> dict[str, Any]:
    if not pricing:
        return {"prompt_per_1k": 0.0, "completion_per_1k": 0.0, "currency": "USD"}
    models = pricing.get("models", {}) if isinstance(pricing, dict) else {}
    entry = models.get(model_name)
    if not isinstance(entry, dict):
        if model_name not in _WARNED_MISSING_PRICING:
            _WARNED_MISSING_PRICING.add(model_name)
            logger.warning("No pricing found for model '%s'; cost will be 0.0", model_name)
        return {"prompt_per_1k": 0.0, "completion_per_1k": 0.0, "currency": "USD"}
    return {
        "prompt_per_1k": float(entry.get("prompt_per_1k", 0.0)),
        "completion_per_1k": float(entry.get("completion_per_1k", 0.0)),
        "currency": str(entry.get("currency", "USD")),
    }


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
        raise RuntimeError("tiktoken is required for billing estimate mode") from exc
    return tiktoken.get_encoding(name)


def _estimate_tokens(messages: list[dict[str, str]], completion_text: str, estimate_cfg: dict[str, Any]) -> tuple[int, int, int]:
    tokenizer_name = str(estimate_cfg.get("tokenizer", "tiktoken"))
    if tokenizer_name.lower() != "tiktoken":
        raise ValueError(f"Unsupported tokenizer for billing estimate: {tokenizer_name}")
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


class BillingTracker:
    def __init__(
        self,
        *,
        model_name: str,
        pricing: dict[str, Any] | None,
        billing: dict[str, Any] | None,
    ) -> None:
        self.model_name = model_name
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.cost = 0.0
        self.api_calls = 0

        billing = billing if isinstance(billing, dict) else {}
        self._billing_mode = str(billing.get("mode", "auto"))
        self._estimate_cfg = billing.get("estimate", {}) if isinstance(billing.get("estimate"), dict) else {}
        self._pricing = _resolve_pricing(model_name, pricing)
        self._resolved_mode = _MODEL_MODE_CACHE.get(model_name)

    def _resolve_mode(self, response: dict[str, Any]) -> str:
        if self._billing_mode != "auto":
            return self._billing_mode
        if self._resolved_mode:
            return self._resolved_mode
        if _extract_usage(response) is not None:
            _MODEL_MODE_CACHE[self.model_name] = "usage"
            self._resolved_mode = "usage"
            return "usage"
        _MODEL_MODE_CACHE[self.model_name] = "estimate"
        self._resolved_mode = "estimate"
        if self.model_name not in _WARNED_MISSING_USAGE:
            _WARNED_MISSING_USAGE.add(self.model_name)
            logger.warning(
                "Model '%s' returned no usage; falling back to tiktoken estimate mode", self.model_name
            )
        return "estimate"

    def add_call(
        self,
        *,
        messages: list[dict[str, str]],
        response: dict[str, Any],
        completion_text: str,
    ) -> dict[str, Any]:
        self.api_calls += 1
        mode = self._resolve_mode(response)
        if mode == "usage":
            usage = _extract_usage(response)
            if usage is None:
                mode = "estimate"
            else:
                prompt_tokens, completion_tokens, total_tokens = usage
        if mode == "estimate":
            prompt_tokens, completion_tokens, total_tokens = _estimate_tokens(messages, completion_text, self._estimate_cfg)

        prompt_price = float(self._pricing.get("prompt_per_1k", 0.0))
        completion_price = float(self._pricing.get("completion_per_1k", 0.0))
        cost = (prompt_tokens / 1000.0) * prompt_price + (completion_tokens / 1000.0) * completion_price

        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens
        self.cost += cost
        self._resolved_mode = mode
        _MODEL_MODE_CACHE[self.model_name] = mode

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": cost,
            "billing_mode": mode,
        }

    def summary(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": self.cost,
            "api_calls": self.api_calls,
            "billing_mode": self._resolved_mode or self._billing_mode,
            "pricing": self._pricing,
        }
