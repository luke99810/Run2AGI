from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from codentum_contracts import ModelRouting, Usage
from codentum_contracts.interfaces import ModelMessage, ModelRequest, ToolSchema
from codentum_harness.model_gateway import (
    AnthropicGateway,
    MissingModelPricingError,
    ModelGatewayPolicy,
    ModelIsolationError,
    OpenAICompatibleGateway,
    TokenPricing,
)
from codentum_roles import load_builtin_role_specs


def model_request() -> ModelRequest:
    return ModelRequest(
        system="You are Codentum.",
        messages=(ModelMessage(role="user", content="Implement the packet."),),
        tools=(
            ToolSchema(
                name="write_file",
                description="write a file",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            ),
        ),
    )


def test_openai_compatible_gateway_invokes_chat_completion_and_prices_usage() -> None:
    client = FakeOpenAIClient(
        response=SimpleNamespace(
            choices=(
                SimpleNamespace(
                    message=SimpleNamespace(content="done", tool_calls=None),
                    finish_reason="stop",
                ),
            ),
            usage=SimpleNamespace(
                prompt_tokens=1000,
                completion_tokens=2000,
                prompt_tokens_details=SimpleNamespace(cached_tokens=100),
            ),
        )
    )
    gateway = OpenAICompatibleGateway(
        client=client,
        pricing={"qwen-plus": TokenPricing(1.0, 2.0, cached_input_per_million_usd=0.1)},
    )

    response = asyncio.run(_invoke_once(gateway, "coder", "qwen-plus", model_request()))

    assert response.text == "done"
    assert response.stop_reason == "end"
    assert response.usage == Usage(
        cost_usd=0.00491,
        input_tokens=1000,
        output_tokens=2000,
        cached_input_tokens=100,
    )
    assert client.requests[0]["model"] == "qwen-plus"
    assert client.requests[0]["messages"][0] == {"role": "system", "content": "You are Codentum."}
    assert client.requests[0]["tools"][0]["function"]["name"] == "write_file"
    ledger = asyncio.run(gateway.ledger())
    assert ledger.total_usd == pytest.approx(0.00491)
    assert ledger.by_role == {"coder": pytest.approx(0.00491)}


def test_openai_compatible_gateway_parses_tool_calls() -> None:
    client = FakeOpenAIClient(
        response=SimpleNamespace(
            choices=(
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=None,
                        tool_calls=(
                            SimpleNamespace(
                                id="call-1",
                                function=SimpleNamespace(
                                    name="write_file",
                                    arguments='{"path": "README.md", "content": "ok"}',
                                ),
                            ),
                        ),
                    ),
                    finish_reason="tool_calls",
                ),
            ),
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )
    )
    gateway = OpenAICompatibleGateway(
        client=client,
        pricing={"qwen-plus": TokenPricing(1.0, 2.0)},
    )

    response = asyncio.run(_invoke_once(gateway, "coder", "qwen-plus", model_request()))

    assert response.stop_reason == "tool_use"
    assert response.tool_calls[0].id == "call-1"
    assert response.tool_calls[0].name == "write_file"
    assert response.tool_calls[0].input == {"path": "README.md", "content": "ok"}


def test_openai_compatible_gateway_accepts_bailian_text_response_without_pricing() -> None:
    gateway = OpenAICompatibleGateway(
        client=FakeOpenAIClient(
            response=SimpleNamespace(
                choices=None,
                text="done",
                finish_reason="stop",
                usage=None,
            )
        ),
        pricing={},
        require_pricing=False,
    )

    response = asyncio.run(_invoke_once(gateway, "reviewer", "qwen-max", model_request()))

    assert response.text == "done"
    assert response.stop_reason == "end"
    assert response.tool_calls == ()
    assert response.usage == Usage(
        cost_usd=0.0,
        input_tokens=0,
        output_tokens=0,
        cached_input_tokens=0,
    )


def test_openai_compatible_gateway_requires_usage_when_response_is_priced() -> None:
    gateway = OpenAICompatibleGateway(
        client=FakeOpenAIClient(
            response=SimpleNamespace(
                choices=None,
                text="done",
                finish_reason="stop",
                usage=None,
            )
        ),
        pricing={"qwen-max": TokenPricing(1.0, 2.0)},
    )

    with pytest.raises(ValueError, match="usage"):
        asyncio.run(_invoke_once(gateway, "reviewer", "qwen-max", model_request()))


def test_gateway_requires_pricing_by_default() -> None:
    gateway = OpenAICompatibleGateway(
        client=FakeOpenAIClient(success_openai_response()),
        pricing={},
    )

    with pytest.raises(MissingModelPricingError, match="qwen-plus"):
        asyncio.run(gateway.open("coder", ModelRouting(model="qwen-plus", effort="medium"), 1.0))


def test_gateway_policy_enforces_model_isolation() -> None:
    policy = ModelGatewayPolicy(
        role_models={"coder": "qwen-plus"},
        must_differ_from={"reviewer": ("coder",)},
    )
    gateway = OpenAICompatibleGateway(
        client=FakeOpenAIClient(success_openai_response()),
        pricing={"qwen-plus": TokenPricing(1.0, 2.0)},
        policy=policy,
    )

    with pytest.raises(ModelIsolationError, match="reviewer"):
        asyncio.run(gateway.open("reviewer", ModelRouting(model="qwen-plus", effort="high"), 1.0))


def test_gateway_policy_can_compare_model_families() -> None:
    policy = ModelGatewayPolicy(
        role_models={"coder": "qwen-plus"},
        must_differ_from={"reviewer": ("coder",)},
        compare_families=True,
    )
    gateway = OpenAICompatibleGateway(
        client=FakeOpenAIClient(success_openai_response()),
        pricing={"qwen-max": TokenPricing(1.0, 2.0)},
        policy=policy,
    )

    with pytest.raises(ModelIsolationError, match="family"):
        asyncio.run(gateway.open("reviewer", ModelRouting(model="qwen-max", effort="high"), 1.0))


def test_gateway_policy_compares_namespaced_model_families() -> None:
    policy = ModelGatewayPolicy(
        role_models={"coder": "siliconflow/deepseek-v3.2"},
        must_differ_from={"reviewer": ("coder",)},
        compare_families=True,
    )

    with pytest.raises(ModelIsolationError, match="family"):
        policy.validate_open("reviewer", ModelRouting(model="deepseek-v4-pro", effort="high"))


def test_builtin_role_specs_keep_reviewer_and_qa_off_coder_model() -> None:
    specs = load_builtin_role_specs()
    by_role = {spec.id: spec for spec in specs}
    policy = ModelGatewayPolicy.from_role_specs(specs)
    coder_policy = by_role["coder"].modelPolicy
    assert coder_policy is not None
    coder_model = coder_policy.defaultModel
    assert coder_model is not None
    assert coder_model == "qwen-coder-plus-1106"

    for role in ("reviewer", "qa"):
        role_policy = by_role[role].modelPolicy
        assert role_policy is not None
        role_model = role_policy.defaultModel
        assert role_model is not None
        assert role_model != coder_model
        policy.validate_open(role, ModelRouting(model=role_model, effort="high"))
        with pytest.raises(ModelIsolationError, match=role):
            policy.validate_open(role, ModelRouting(model=coder_model, effort="high"))


def test_anthropic_gateway_invokes_messages_api_and_prices_usage() -> None:
    client = FakeAnthropicClient(
        response=SimpleNamespace(
            content=(SimpleNamespace(type="text", text="done"),),
            stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=500, output_tokens=100),
        )
    )
    gateway = AnthropicGateway(
        client=client,
        pricing={"claude-sonnet-4": TokenPricing(3.0, 15.0)},
        default_max_tokens=512,
    )

    response = asyncio.run(_invoke_once(gateway, "reviewer", "claude-sonnet-4", model_request()))

    assert response.text == "done"
    assert response.stop_reason == "end"
    assert response.usage == Usage(
        cost_usd=0.003,
        input_tokens=500,
        output_tokens=100,
        cached_input_tokens=0,
    )
    assert client.requests[0]["model"] == "claude-sonnet-4"
    assert client.requests[0]["system"] == "You are Codentum."
    assert client.requests[0]["max_tokens"] == 512
    assert client.requests[0]["tools"][0]["name"] == "write_file"


def success_openai_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=(
            SimpleNamespace(
                message=SimpleNamespace(content="done", tool_calls=None),
                finish_reason="stop",
            ),
        ),
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
    )


async def _invoke_once(
    gateway: object,
    role: str,
    model: str,
    req: ModelRequest,
) -> Any:
    assert hasattr(gateway, "open")
    session = await gateway.open(role, ModelRouting(model=model, effort="medium"), 1.0)
    try:
        return await session.invoke(req)
    finally:
        await session.close()


class FakeOpenAIClient:
    def __init__(self, response: SimpleNamespace) -> None:
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=FakeOpenAICompletions(self.requests, response))


class FakeOpenAICompletions:
    def __init__(self, requests: list[dict[str, Any]], response: SimpleNamespace) -> None:
        self.requests = requests
        self.response = response

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        return self.response


class FakeAnthropicClient:
    def __init__(self, response: SimpleNamespace) -> None:
        self.requests: list[dict[str, Any]] = []
        self.messages = FakeAnthropicMessages(self.requests, response)


class FakeAnthropicMessages:
    def __init__(self, requests: list[dict[str, Any]], response: SimpleNamespace) -> None:
        self.requests = requests
        self.response = response

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(kwargs)
        return self.response
