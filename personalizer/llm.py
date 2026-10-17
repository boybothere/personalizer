"""Thin wrapper over the Anthropic SDK: structured outputs, usage/cost accounting, budget cap."""

from __future__ import annotations

import logging
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .config import LLM_PRICES
from .models import Usage

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


class StructuredLLM(Protocol):
    def parse(self, system: str, user: str, schema: type[T], max_tokens: int = 8000) -> T: ...


class AnthropicLLM:
    def __init__(self, model: str, effort: str, usage: Usage, max_cost_usd: float, client=None):
        import anthropic

        self._anthropic = anthropic
        self.client = client or anthropic.Anthropic(max_retries=3)
        self.model = model
        self.effort = effort
        self.usage = usage
        self.max_cost = max_cost_usd
        self.prices = LLM_PRICES.get(model, (5.0, 25.0))  # conservative default for unknown models

    def _account(self, input_tokens: int, output_tokens: int) -> None:
        self.usage.llm_calls += 1
        self.usage.llm_input_tokens += input_tokens
        self.usage.llm_output_tokens += output_tokens
        pin, pout = self.prices
        self.usage.estimated_cost_usd += input_tokens * pin / 1e6 + output_tokens * pout / 1e6

    def parse(self, system: str, user: str, schema: type[T], max_tokens: int = 8000) -> T:
        if self.usage.estimated_cost_usd >= self.max_cost:
            raise BudgetExceeded(f"per-run LLM budget of ${self.max_cost:.2f} reached")
        a = self._anthropic
        try:
            response = self.client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                output_config={"effort": self.effort},
                # On a safety-classifier decline, let the API re-run on its recommended fallback model.
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
            )
        except a.RateLimitError as exc:
            raise LLMError("LLM rate limited") from exc
        except a.AuthenticationError as exc:
            raise LLMError("LLM authentication failed; check ANTHROPIC_API_KEY") from exc
        except a.APIStatusError as exc:
            raise LLMError(f"LLM API error {exc.status_code}") from exc
        except a.APIConnectionError as exc:
            raise LLMError("LLM network error") from exc
        except ValidationError as exc:
            # parse() validates before we can see stop_reason; a refusal or truncation lands here.
            self.usage.llm_calls += 1
            raise LLMError("LLM output did not match the schema (refusal or truncated output)") from exc
        self._account(response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason == "refusal":
            raise LLMError("LLM declined the request")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise LLMError("LLM output incomplete")
        return response.parsed_output
