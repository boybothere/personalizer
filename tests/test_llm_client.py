"""Exercises the real Anthropic SDK request path against a mock transport (no network, no cost)."""

import json

import anthropic
import httpx2
import pytest

from personalizer.extract import ExtractionResult
from personalizer.llm import AnthropicLLM, BudgetExceeded, LLMError
from personalizer.models import Usage


def make_client(reply: dict, seen: dict):
    def handler(req: httpx2.Request):
        seen["body"] = json.loads(req.content)
        seen["beta"] = req.headers.get("anthropic-beta")
        return httpx2.Response(200, json=reply)

    return anthropic.Anthropic(api_key="test-key", max_retries=0,
                               http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))


def reply(text: str, stop: str = "end_turn") -> dict:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 200}}


def test_structured_request_shape_and_cost_accounting():
    seen: dict = {}
    payload = {"same_person": "same", "claims": []}
    usage = Usage()
    llm = AnthropicLLM("claude-opus-5-5", "low", usage, 1.0, client=make_client(reply(json.dumps(payload)), seen))
    out = llm.parse("sys", "user", ExtractionResult)
    assert out.same_person == "same"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in seen["beta"]
    assert usage.llm_calls == 1 and usage.estimated_cost_usd == pytest.approx(1000 * 4 / 1e6 + 200 * 20 / 1e6)


def test_refusal_raises():
    llm = AnthropicLLM("claude-opus-5-5", "low", Usage(), 1.0, client=make_client(reply("", "refusal"), {}))
    with pytest.raises(LLMError, match="refusal"):
        llm.parse("sys", "user", ExtractionResult)


def test_budget_cap():
    usage = Usage(estimated_cost_usd=2.0)
    llm = AnthropicLLM("claude-opus-5-5", "low", usage, 1.0, client=make_client(reply("{}"), {}))
    with pytest.raises(BudgetExceeded):
        llm.parse("sys", "user", ExtractionResult)
