"""Anthropic ModelGateway implementation."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal, cast
from uuid import uuid4

from codentum_contracts.interfaces import (
    CostEstimate,
    CostLedger,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelSession,
    ToolCall,
    Usage,
)
from codentum_contracts.state import ModelId, ModelRouting, RoleId

from .openai_compatible import _cost_or_zero, _now_iso
from .policy import ModelGatewayPolicy
from .pricing import MissingModelPricingError, TokenPricing

__all__ = [
    "AnthropicGateway",
]


class AnthropicGateway:
    """ModelGateway backed by the official Anthropic Messages API."""

    def __init__(
        self,
        *,
        client: Any,
        pricing: Mapping[ModelId, TokenPricing],
        policy: ModelGatewayPolicy | None = None,
        require_pricing: bool = True,
        default_max_tokens: int = 2048,
    ) -> None:
        if default_max_tokens <= 0:
            raise ValueError("default_max_tokens must be positive")
        self._client = client
        self._pricing = dict(pricing)
        self._policy = policy or ModelGatewayPolicy()
        self._require_pricing = require_pricing
        self._default_max_tokens = default_max_tokens
        self._since = _now_iso()
        self._total_usd = 0.0
        self._by_role: dict[str, float] = {}
        self._by_model: dict[str, float] = {}

    @classmethod
    def from_env(
        cls,
        *,
        pricing: Mapping[ModelId, TokenPricing],
        policy: ModelGatewayPolicy | None = None,
        api_key_env: str = "ANTHROPIC_API_KEY",
        require_pricing: bool = True,
        timeout_seconds: float = 120.0,
        default_max_tokens: int = 2048,
    ) -> AnthropicGateway:
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise RuntimeError(f"{api_key_env} is not set")
        try:
            anthropic_module = cast(Any, import_module("anthropic"))
        except ImportError as exc:  # pragma: no cover - exercised only without optional dependency.
            raise RuntimeError("anthropic package is required for AnthropicGateway") from exc

        return cls(
            client=anthropic_module.AsyncAnthropic(api_key=api_key, timeout=timeout_seconds),
            pricing=pricing,
            policy=policy,
            require_pricing=require_pricing,
            default_max_tokens=default_max_tokens,
        )

    async def open(self, role: RoleId, routing: ModelRouting, grant_usd: float) -> ModelSession:
        if grant_usd <= 0:
            raise ValueError("grant_usd must be positive")
        self._policy.validate_open(role, routing)
        price = self._price_for_model(routing.model)
        return _AnthropicSession(
            gateway=self,
            client=self._client,
            role=role,
            model=routing.model,
            grant_usd=grant_usd,
            price=price,
            default_max_tokens=self._default_max_tokens,
        )

    async def estimate(self, routing: ModelRouting, req: ModelRequest) -> CostEstimate:
        price = self._price_for_model(routing.model)
        input_chars = len(req.system) + sum(len(message.content) for message in req.messages)
        input_tokens = max(1, (input_chars + 3) // 4)
        estimated = _cost_or_zero(
            price,
            input_tokens=input_tokens,
            output_tokens=self._default_max_tokens,
            cached_input_tokens=0,
        )
        return CostEstimate(estimated_usd=estimated, upper_bound_usd=estimated * 2)

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


@dataclass(slots=True)
class _AnthropicSession:
    gateway: AnthropicGateway
    client: Any
    role: RoleId
    model: ModelId
    grant_usd: float
    price: TokenPricing | None
    default_max_tokens: int
    session_id: str = ""
    _spent_usd: float = 0.0

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = f"anthropic-{uuid4().hex}"

    async def invoke(self, req: ModelRequest) -> ModelResponse:
        response = await self.client.messages.create(
            model=self.model,
            system=req.system,
            messages=_to_anthropic_messages(req.messages),
            tools=_to_anthropic_tools(req) or None,
            max_tokens=self.default_max_tokens,
        )
        model_response = _parse_anthropic_response(response, price=self.price)
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


def _to_anthropic_messages(messages: Sequence[ModelMessage]) -> list[dict[str, str]]:
    return [{"role": message.role, "content": message.content} for message in messages]


def _to_anthropic_tools(req: ModelRequest) -> list[dict[str, object]]:
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "input_schema": dict(tool.input_schema),
        }
        for tool in req.tools
    ]


def _parse_anthropic_response(response: object, *, price: TokenPricing | None) -> ModelResponse:
    content_blocks = _object_sequence(_required_field(response, "content"), "content")
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in content_blocks:
        block_type = _optional_string(_field(block, "type")) or ""
        if block_type == "text":
            text_parts.append(_optional_string(_field(block, "text")) or "")
        elif block_type == "tool_use":
            tool_calls.append(
                ToolCall(
                    id=_string_field(block, "id"),
                    name=_string_field(block, "name"),
                    input=_mapping_field(block, "input"),
                )
            )

    usage = _parse_anthropic_usage(_required_field(response, "usage"), price=price)
    stop_reason = _optional_string(_field(response, "stop_reason")) or "end_turn"
    return ModelResponse(
        text="".join(text_parts),
        tool_calls=tuple(tool_calls),
        stop_reason=_map_anthropic_stop_reason(stop_reason),
        usage=usage,
    )


def _parse_anthropic_usage(raw_usage: object, *, price: TokenPricing | None) -> Usage:
    input_tokens = _int_field_any(raw_usage, ("input_tokens",))
    output_tokens = _int_field_any(raw_usage, ("output_tokens",))
    cached_input_tokens = _int_field_any(raw_usage, ("cache_read_input_tokens",), default=0)
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


def _map_anthropic_stop_reason(reason: str) -> Literal["end", "tool_use", "max_output", "refusal"]:
    match reason:
        case "end_turn" | "stop_sequence" | "end":
            return "end"
        case "tool_use":
            return "tool_use"
        case "max_tokens" | "max_output":
            return "max_output"
        case "refusal":
            return "refusal"
        case _:
            return "refusal"


def _field(obj: object, name: str) -> object | None:
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def _required_field(obj: object, name: str) -> object:
    value = _field(obj, name)
    if value is None:
        raise ValueError(f"missing response field {name!r}")
    return value


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


def _mapping_field(obj: object, name: str) -> Mapping[str, Any]:
    value = _required_field(obj, name)
    if not isinstance(value, Mapping):
        raise ValueError(f"response field {name!r} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _stream_not_supported(req: ModelRequest) -> AsyncIterator[Any]:
    return _UnsupportedStream(req)


class _UnsupportedStream:
    def __init__(self, req: ModelRequest) -> None:
        self._req = req

    def __aiter__(self) -> _UnsupportedStream:
        return self

    async def __anext__(self) -> Any:
        raise NotImplementedError("streaming is not implemented for AnthropicGateway yet")
