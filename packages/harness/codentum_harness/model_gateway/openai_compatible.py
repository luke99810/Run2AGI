"""OpenAI-compatible ModelGateway implementation."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Literal, cast
from uuid import uuid4

from codentum_contracts.interfaces import (
    CostEstimate,
    CostLedger,
    ModelRequest,
    ModelResponse,
    ModelSession,
    ToolCall,
    Usage,
)
from codentum_contracts.state import ModelId, ModelRouting, RoleId

from .policy import ModelGatewayPolicy
from .pricing import MissingModelPricingError, TokenPricing

__all__ = [
    "BailianOpenAICompatibleGateway",
    "OpenAICompatibleGateway",
]


class OpenAICompatibleGateway:
    """ModelGateway backed by an OpenAI-compatible chat-completions client."""

    def __init__(
        self,
        *,
        client: Any,
        pricing: Mapping[ModelId, TokenPricing],
        policy: ModelGatewayPolicy | None = None,
        require_pricing: bool = True,
        default_estimated_output_tokens: int = 2048,
    ) -> None:
        if default_estimated_output_tokens <= 0:
            raise ValueError("default_estimated_output_tokens must be positive")
        self._client = client
        self._pricing = dict(pricing)
        self._policy = policy or ModelGatewayPolicy()
        self._require_pricing = require_pricing
        self._default_estimated_output_tokens = default_estimated_output_tokens
        self._since = _now_iso()
        self._total_usd = 0.0
        self._by_role: dict[str, float] = {}
        self._by_model: dict[str, float] = {}

    @classmethod
    def from_sdk(
        cls,
        *,
        api_key: str,
        base_url: str,
        pricing: Mapping[ModelId, TokenPricing],
        policy: ModelGatewayPolicy | None = None,
        require_pricing: bool = True,
        timeout_seconds: float = 120.0,
    ) -> OpenAICompatibleGateway:
        """Create a gateway with the official OpenAI Python SDK."""

        try:
            openai_module = cast(Any, import_module("openai"))
        except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
            raise RuntimeError("openai package is required for OpenAI-compatible providers") from exc

        client = openai_module.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
        )
        return cls(
            client=client,
            pricing=pricing,
            policy=policy,
            require_pricing=require_pricing,
        )

    async def open(self, role: RoleId, routing: ModelRouting, grant_usd: float) -> ModelSession:
        if grant_usd <= 0:
            raise ValueError("grant_usd must be positive")
        self._policy.validate_open(role, routing)
        price = self._price_for_model(routing.model)
        return _OpenAICompatibleSession(
            gateway=self,
            client=self._client,
            role=role,
            model=routing.model,
            grant_usd=grant_usd,
            price=price,
        )

    async def estimate(self, routing: ModelRouting, req: ModelRequest) -> CostEstimate:
        price = self._price_for_model(routing.model)
        input_tokens = _estimate_tokens(req)
        output_tokens = self._default_estimated_output_tokens
        estimated = _cost_or_zero(
            price,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=0,
        )
        return CostEstimate(
            estimated_usd=estimated,
            upper_bound_usd=estimated * 2,
        )

    async def ledger(self) -> CostLedger:
        return CostLedger(
            total_usd=self._total_usd,
            by_role=dict(sorted(self._by_role.items())),
            by_model=dict(sorted(self._by_model.items())),
            since=self._since,
        )

    def _record_spend(self, *, role: RoleId, model: ModelId, cost_usd: float) -> None:
        self._total_usd += cost_usd
        self._by_role[str(role)] = self._by_role.get(str(role), 0.0) + cost_usd
        self._by_model[str(model)] = self._by_model.get(str(model), 0.0) + cost_usd

    def _price_for_model(self, model: ModelId) -> TokenPricing | None:
        price = self._pricing.get(model)
        if price is None and self._require_pricing:
            raise MissingModelPricingError(f"missing token pricing for model {model!r}")
        return price


class BailianOpenAICompatibleGateway(OpenAICompatibleGateway):
    """OpenAI-compatible gateway with Bailian/DashScope defaults."""

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    API_KEY_ENVS = ("DASHSCOPE_API_KEY", "BAILIAN_API_KEY", "QWEN_API_KEY", "AGENTTEAMS_LLM_API_KEY")

    @classmethod
    def from_env(
        cls,
        *,
        pricing: Mapping[ModelId, TokenPricing],
        policy: ModelGatewayPolicy | None = None,
        api_key_envs: Sequence[str] = API_KEY_ENVS,
        base_url_env: str = "BAILIAN_BASE_URL",
        require_pricing: bool = True,
        timeout_seconds: float = 120.0,
    ) -> BailianOpenAICompatibleGateway:
        api_key = _first_env(api_key_envs)
        base_url = os.environ.get(base_url_env, cls.DEFAULT_BASE_URL)
        gateway = cls.from_sdk(
            api_key=api_key,
            base_url=base_url,
            pricing=pricing,
            policy=policy,
            require_pricing=require_pricing,
            timeout_seconds=timeout_seconds,
        )
        return cast(BailianOpenAICompatibleGateway, gateway)


@dataclass(slots=True)
class _OpenAICompatibleSession:
    gateway: OpenAICompatibleGateway
    client: Any
    role: RoleId
    model: ModelId
    grant_usd: float
    price: TokenPricing | None
    session_id: str = ""
    _spent_usd: float = 0.0

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = f"openai-compatible-{uuid4().hex}"

    async def invoke(self, req: ModelRequest) -> ModelResponse:
        response = await self.client.chat.completions.create(**_to_chat_completion_kwargs(self.model, req))
        model_response = _parse_chat_completion_response(response, price=self.price)
        self._spent_usd += model_response.usage.cost_usd
        self.gateway._record_spend(
            role=self.role,
            model=self.model,
            cost_usd=model_response.usage.cost_usd,
        )
        return model_response

    def stream(self, req: ModelRequest) -> AsyncIterator[Any]:
        return _stream_not_supported(req)

    def spent_usd(self) -> float:
        return self._spent_usd

    async def close(self) -> None:
        return None


def _to_chat_completion_kwargs(model: ModelId, req: ModelRequest) -> dict[str, object]:
    messages: list[dict[str, str]] = [{"role": "system", "content": req.system}]
    messages.extend({"role": message.role, "content": message.content} for message in req.messages)

    payload: dict[str, object] = {
        "model": model,
        "messages": messages,
    }
    if req.tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": dict(tool.input_schema),
                },
            }
            for tool in req.tools
        ]
        payload["tool_choice"] = "auto"
    return payload


def _parse_chat_completion_response(response: object, *, price: TokenPricing | None) -> ModelResponse:
    choices = _sequence_field(response, "choices")
    if not choices:
        raise ValueError("chat completion response has no choices")

    choice = choices[0]
    message = _required_field(choice, "message")
    content = _optional_string(_field(message, "content")) or ""
    usage = _parse_openai_usage(_required_field(response, "usage"), price=price)
    finish_reason = _optional_string(_field(choice, "finish_reason")) or "stop"
    return ModelResponse(
        text=content,
        tool_calls=tuple(_parse_openai_tool_calls(_field(message, "tool_calls"))),
        stop_reason=_map_openai_stop_reason(finish_reason),
        usage=usage,
    )


def _parse_openai_usage(raw_usage: object, *, price: TokenPricing | None) -> Usage:
    input_tokens = _int_field_any(raw_usage, ("prompt_tokens", "input_tokens"))
    output_tokens = _int_field_any(raw_usage, ("completion_tokens", "output_tokens"))
    cached_input_tokens = _cached_openai_tokens(raw_usage)
    return Usage(
        cost_usd=_cost_or_zero(
            price,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
        ),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )


def _cached_openai_tokens(raw_usage: object) -> int:
    details = _field(raw_usage, "prompt_tokens_details")
    if details is None:
        details = _field(raw_usage, "input_tokens_details")
    if details is None:
        return 0
    return _int_field_any(details, ("cached_tokens", "cached_input_tokens"), default=0)


def _parse_openai_tool_calls(raw_tool_calls: object | None) -> list[ToolCall]:
    if raw_tool_calls is None:
        return []
    calls: list[ToolCall] = []
    for raw_call in _object_sequence(raw_tool_calls, "tool_calls"):
        function = _required_field(raw_call, "function")
        calls.append(
            ToolCall(
                id=_string_field(raw_call, "id"),
                name=_string_field(function, "name"),
                input=_json_mapping_field(function, "arguments"),
            )
        )
    return calls


def _map_openai_stop_reason(reason: str) -> Literal["end", "tool_use", "max_output", "refusal"]:
    match reason:
        case "stop" | "end_turn" | "end":
            return "end"
        case "tool_calls" | "tool_use":
            return "tool_use"
        case "length" | "max_tokens" | "max_output":
            return "max_output"
        case "content_filter" | "refusal":
            return "refusal"
        case _:
            return "refusal"


def _estimate_tokens(req: ModelRequest) -> int:
    text = req.system + "\n" + "\n".join(message.content for message in req.messages)
    tool_chars = sum(
        len(tool.name) + len(tool.description) + len(json.dumps(tool.input_schema))
        for tool in req.tools
    )
    # Deterministic approximation. Real admission can replace this with provider tokenizers later.
    return max(1, (len(text) + tool_chars + 3) // 4)


def _cost_or_zero(
    price: TokenPricing | None,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int,
) -> float:
    if price is None:
        return 0.0
    return price.cost_usd(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
    )


def _first_env(names: Sequence[str]) -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    raise RuntimeError(f"none of the API key environment variables are set: {', '.join(names)}")


def _now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _field(obj: object, name: str) -> object | None:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _required_field(obj: object, name: str) -> object:
    value = _field(obj, name)
    if value is None:
        raise ValueError(f"missing response field {name!r}")
    return value


def _sequence_field(obj: object, name: str) -> Sequence[object]:
    return _object_sequence(_required_field(obj, name), name)


def _object_sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise ValueError(f"response field {name!r} must be a sequence")
    return value


def _optional_string(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("response string field must be a string")
    return value


def _string_field(obj: object, name: str) -> str:
    value = _optional_string(_required_field(obj, name))
    if not value:
        raise ValueError(f"response field {name!r} must be a non-empty string")
    return value


def _int_field_any(obj: object, names: Sequence[str], *, default: int | None = None) -> int:
    for name in names:
        value = _field(obj, name)
        if value is not None:
            if not isinstance(value, int):
                raise ValueError(f"response field {name!r} must be an integer")
            return value
    if default is not None:
        return default
    raise ValueError(f"missing integer response field, tried: {', '.join(names)}")


def _json_mapping_field(obj: object, name: str) -> Mapping[str, Any]:
    raw = _required_field(obj, name)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"response field {name!r} must be JSON object text") from exc
    else:
        parsed = raw
    if not isinstance(parsed, Mapping):
        raise ValueError(f"response field {name!r} must be a JSON object")
    return {str(key): value for key, value in parsed.items()}


def _stream_not_supported(req: ModelRequest) -> AsyncIterator[Any]:
    return _UnsupportedStream(req)


class _UnsupportedStream:
    def __init__(self, req: ModelRequest) -> None:
        self._req = req

    def __aiter__(self) -> _UnsupportedStream:
        return self

    async def __anext__(self) -> Any:
        raise NotImplementedError("streaming is not implemented for OpenAICompatibleGateway yet")
